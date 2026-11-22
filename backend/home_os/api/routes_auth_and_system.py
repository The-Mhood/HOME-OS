import asyncio
import json
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from home_os.api.deps import get_current_user, get_current_user_and_session, get_db, get_runtime
from home_os.api.serializers import (
    serialize_audit,
    serialize_comm_item,
    serialize_command,
    serialize_device,
    serialize_media_session,
    serialize_room,
    serialize_session,
    serialize_user,
)
from home_os.db.models import (
    ActiveMediaSession,
    AuditLog,
    AutomationRule,
    AutomationRun,
    CommandExecution,
    CommunicationItem,
    Device,
    Room,
    User,
    UserSession,
)
from home_os.runtime import HomeOsRuntime
from home_os.schemas.api import LoginRequest, SetupOwnerRequest
from home_os.security.audit import append_audit_log
from home_os.security.auth import create_user_session, resolve_session_user
from home_os.security.crypto import hash_password, verify_password
from home_os.security.rate_limit import RateLimitExceeded

router = APIRouter()


@router.get("/health")
def health_check(runtime: HomeOsRuntime = Depends(get_runtime)):
    from home_os.services.discovery import get_primary_lan_ipv4

    return {
        "status": "ok",
        "version": runtime.settings.app_version,
        "api_revision": "2.0.0",
        "bind_host": runtime.settings.bind_host,
        "bind_port": runtime.settings.bind_port,
        "lan_ip": get_primary_lan_ipv4(),
        "tls_fingerprint": runtime.tls_info.sha256_fingerprint,
    }


@router.get("/system/network-diagnostics")
def network_diagnostics(
    user: User = Depends(get_current_user),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    import socket as _sock
    import sys as _sys
    from pathlib import Path as _Path
    from home_os.services.discovery import get_primary_lan_ipv4, list_lan_interfaces

    lan_ip = get_primary_lan_ipv4()
    ifaces = list_lan_interfaces()
    port = runtime.settings.bind_port

    lan_socket_reachable = False
    if lan_ip != "127.0.0.1":
        try:
            with _sock.create_connection((lan_ip, port), timeout=0.6):
                lan_socket_reachable = True
        except OSError:
            lan_socket_reachable = False

    macos_firewall_enabled = False
    if _sys.platform == "darwin" and _Path("/usr/libexec/ApplicationFirewall/socketfilterfw").exists():
        try:
            fw = subprocess.run(
                ["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            macos_firewall_enabled = "enabled" in (fw.stdout or "").lower()
        except Exception:
            pass

    return {
        "primary_lan_ip": lan_ip,
        "lan_interfaces": ifaces,
        "bind_host": runtime.settings.bind_host,
        "bind_port": port,
        "lan_socket_reachable": lan_socket_reachable,
        "macos_firewall_enabled": macos_firewall_enabled,
    }


@router.get("/auth/status")
def auth_status(db: Session = Depends(get_db), runtime: HomeOsRuntime = Depends(get_runtime)):
    user_count = db.execute(select(func.count()).select_from(User)).scalar_one()
    return {
        "setup_required": user_count == 0,
        "version": runtime.settings.app_version,
        "tls_fingerprint": runtime.tls_info.sha256_fingerprint,
    }


@router.post("/auth/setup")
def setup_owner_account(
    payload: SetupOwnerRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    existing_count = db.execute(select(func.count()).select_from(User)).scalar_one()
    if existing_count > 0:
        raise HTTPException(status_code=403, detail="Owner account has already been initialized")

    owner = User(
        username=payload.username.strip().lower(),
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
        role="owner",
        is_active=True,
    )
    db.add(owner)
    db.flush()

    token, session_obj = create_user_session(
        db,
        user=owner,
        ttl_minutes=runtime.settings.session_ttl_minutes,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
    )
    append_audit_log(
        db,
        severity="info",
        category="auth",
        event_type="owner.initialized",
        actor_type="user",
        actor_id=owner.id,
        ip_address=request.client.host if request.client else None,
        outcome="success",
        details={"username": owner.username},
    )
    db.commit()
    return {
        "access_token": token,
        "expires_at": session_obj.expires_at.isoformat(),
        "user": serialize_user(owner),
    }


@router.post("/auth/login")
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    client_ip = request.client.host if request.client else "unknown"
    try:
        runtime.rate_limiter.check(f"auth:{client_ip}", limit=runtime.settings.auth_rate_limit_per_minute)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=f"Too many login attempts; retry in {exc.retry_after_seconds}s")

    username = payload.username.strip().lower()
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        append_audit_log(
            db,
            severity="warning",
            category="auth",
            event_type="auth.login_failed",
            actor_type="anonymous",
            actor_id=username,
            ip_address=client_ip,
            outcome="denied",
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token, session_obj = create_user_session(
        db,
        user=user,
        ttl_minutes=runtime.settings.session_ttl_minutes,
        ip_address=client_ip,
        user_agent=request.headers.get("User-Agent"),
    )
    append_audit_log(
        db,
        severity="info",
        category="auth",
        event_type="auth.login_succeeded",
        actor_type="user",
        actor_id=user.id,
        ip_address=client_ip,
        outcome="success",
    )
    db.commit()
    return {
        "access_token": token,
        "expires_at": session_obj.expires_at.isoformat(),
        "user": serialize_user(user),
    }


@router.post("/auth/logout")
def logout(
    pair: tuple[User, UserSession] = Depends(get_current_user_and_session),
    db: Session = Depends(get_db),
):
    user, session_obj = pair
    session_obj.revoked_at = datetime.now(timezone.utc)
    append_audit_log(
        db,
        severity="info",
        category="auth",
        event_type="auth.logout",
        actor_type="user",
        actor_id=user.id,
        outcome="success",
    )
    db.commit()
    return {"status": "logged_out"}


@router.get("/auth/me")
def me(pair: tuple[User, UserSession] = Depends(get_current_user_and_session)):
    user, session_obj = pair
    return {"user": serialize_user(user), "session": serialize_session(session_obj, current_id=session_obj.id)}


@router.get("/auth/sessions")
def list_sessions(
    pair: tuple[User, UserSession] = Depends(get_current_user_and_session),
    db: Session = Depends(get_db),
):
    user, current_session = pair
    sessions = db.execute(
        select(UserSession).where(UserSession.user_id == user.id).order_by(UserSession.created_at.desc())
    ).scalars().all()
    return {"sessions": [serialize_session(s, current_id=current_session.id) for s in sessions]}


@router.delete("/auth/sessions/{session_id}")
def revoke_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = db.get(UserSession, session_id)
    if target is None or target.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    target.revoked_at = datetime.now(timezone.utc)
    append_audit_log(
        db,
        severity="warning",
        category="auth",
        event_type="auth.session_revoked",
        actor_type="user",
        actor_id=user.id,
        outcome="revoked",
        details={"session_id": session_id},
    )
    db.commit()
    return {"status": "revoked"}


@router.get("/dashboard/summary")
def dashboard_summary(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    devices = db.execute(select(Device).order_by(Device.last_seen_at.desc())).scalars().all()
    rooms = db.execute(select(Room).order_by(Room.name.asc())).scalars().all()
    dev_name_map = {d.id: d.name for d in devices}

    registered = [d for d in devices if d.lifecycle_state in ("enrolled", "authenticated", "isolated")]
    online = [d for d in registered if d.connection_status == "online"]
    offline = [d for d in registered if d.connection_status != "online"]

    categories: dict[str, int] = {}
    for d in devices:
        categories[d.category] = categories.get(d.category, 0) + 1

    recent_commands = db.execute(
        select(CommandExecution).order_by(CommandExecution.created_at.desc()).limit(12)
    ).scalars().all()
    pending_confirmations = db.execute(
        select(CommandExecution).where(CommandExecution.status == "pending_confirmation")
    ).scalars().all()
    recent_comms = db.execute(
        select(CommunicationItem).where(CommunicationItem.dismissed.is_(False)).order_by(CommunicationItem.received_at.desc()).limit(10)
    ).scalars().all()
    media_sessions = db.execute(select(ActiveMediaSession).order_by(ActiveMediaSession.updated_at.desc())).scalars().all()
    security_events = db.execute(
        select(AuditLog)
        .where(AuditLog.category.in_(["security", "auth", "pairing"]))
        .order_by(AuditLog.id.desc())
        .limit(10)
    ).scalars().all()

    active_rules = db.execute(
        select(func.count()).select_from(AutomationRule).where(AutomationRule.enabled.is_(True))
    ).scalar_one()
    total_rules = db.execute(select(func.count()).select_from(AutomationRule)).scalar_one()
    pending_auto_runs = db.execute(
        select(func.count()).select_from(AutomationRun).where(AutomationRun.status == "awaiting_confirmation")
    ).scalar_one()

    return {
        "counts": {
            "total_discovered": len(devices),
            "registered": len(registered),
            "unpaired": len(devices) - len(registered),
            "online": len(online),
            "offline": len(offline),
            "isolated": sum(1 for d in devices if d.lifecycle_state == "isolated"),
            "pending_confirmations": len(pending_confirmations) + pending_auto_runs,
        },
        "categories": categories,
        "rooms": [serialize_room(r) for r in rooms],
        "devices": [serialize_device(d, include_details=True) for d in devices],
        "recent_activity": [serialize_command(c, dev_name_map.get(c.device_id)) for c in recent_commands],
        "pending_commands": [serialize_command(c, dev_name_map.get(c.device_id)) for c in pending_confirmations],
        "notifications": [serialize_comm_item(i, dev_name_map.get(i.device_id)) for i in recent_comms],
        "active_media_sessions": [serialize_media_session(m, dev_name_map.get(m.device_id)) for m in media_sessions],
        "security_events": [serialize_audit(a) for a in security_events],
        "automation_status": {
            "active_rules": active_rules,
            "total_rules": total_rules,
            "awaiting_confirmation_runs": pending_auto_runs,
        },
    }


@router.get("/system/settings")
def system_settings(
    _user: User = Depends(get_current_user),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    interfaces = []
    try:
        proc = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=2, check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            raw_ifaces = json.loads(proc.stdout)
            for iface in raw_ifaces:
                addrs = [
                    f"{a.get('local')}/{a.get('prefixlen')}"
                    for a in iface.get("addr_info", [])
                    if a.get("local")
                ]
                interfaces.append(
                    {
                        "name": iface.get("ifname"),
                        "operstate": iface.get("operstate"),
                        "mac": iface.get("address"),
                        "addresses": addrs,
                    }
                )
    except Exception:
        pass

    return {
        "app_name": runtime.settings.app_name,
        "version": runtime.settings.app_version,
        "bind_host": runtime.settings.bind_host,
        "bind_port": runtime.settings.bind_port,
        "database_dialect": runtime.engine.dialect.name,
        "tls_cert_path": str(runtime.tls_info.cert_path),
        "tls_sha256_fingerprint": runtime.tls_info.sha256_fingerprint,
        "session_ttl_minutes": runtime.settings.session_ttl_minutes,
        "pairing_ttl_seconds": runtime.settings.pairing_ttl_seconds,
        "replay_window_seconds": runtime.settings.replay_window_seconds,
        "discovery_protocols": ["mDNS/DNS-SD", "SSDP/UPnP", "ARP/Neighbour", "Manual/QR"],
        "interfaces": interfaces,
    }


@router.websocket("/ws")
async def operator_events_websocket(
    websocket: WebSocket,
    token: str = Query(default=""),
):
    runtime: HomeOsRuntime = websocket.app.state.runtime
    with runtime.SessionLocal() as db:
        resolved = resolve_session_user(db, token)
        if resolved is None:
            await websocket.close(code=4401, reason="Unauthorized session")
            return

    await websocket.accept()
    queue = runtime.event_bus.create_queue()
    try:
        await websocket.send_json({"event_type": "ws.connected", "payload": {"status": "subscribed"}})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20.0)
                await websocket.send_json(event)
            except (TimeoutError, asyncio.TimeoutError):
                await websocket.send_json({"event_type": "ws.heartbeat", "payload": {}})
    except WebSocketDisconnect:
        pass
    finally:
        runtime.event_bus.remove_queue(queue)
