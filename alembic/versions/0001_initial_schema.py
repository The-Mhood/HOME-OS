"""Initial HOME OS schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-21 00:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"])

    op.create_table(
        "rooms",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("floor", sa.String(40), nullable=True),
        sa.Column("icon", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("hardware_fingerprint", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(48), nullable=False),
        sa.Column("manufacturer", sa.String(80), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("os_name", sa.String(64), nullable=True),
        sa.Column("os_version", sa.String(64), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("mac_address", sa.String(32), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("hostname", sa.String(128), nullable=True),
        sa.Column("room_id", sa.String(36), sa.ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True),
        sa.Column("group_tags", sa.JSON(), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("discovery_source", sa.String(48), nullable=False),
        sa.Column("adapter_type", sa.String(48), nullable=False),
        sa.Column("connection_status", sa.String(32), nullable=False),
        sa.Column("battery_level", sa.Integer(), nullable=True),
        sa.Column("is_charging", sa.Boolean(), nullable=True),
        sa.Column("health_score", sa.Integer(), nullable=True),
        sa.Column("integration_health", sa.String(32), nullable=False),
        sa.Column("health_details_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_devices_hardware_fingerprint", "devices", ["hardware_fingerprint"])
    op.create_index("ix_devices_category", "devices", ["category"])
    op.create_index("ix_devices_ip_address", "devices", ["ip_address"])
    op.create_index("ix_devices_room_id", "devices", ["room_id"])
    op.create_index("ix_devices_lifecycle_state", "devices", ["lifecycle_state"])
    op.create_index("ix_devices_adapter_type", "devices", ["adapter_type"])
    op.create_index("ix_devices_connection_status", "devices", ["connection_status"])

    op.create_table(
        "device_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_hmac_secret", sa.Text(), nullable=False),
        sa.Column("client_cert_fingerprint", sa.String(128), nullable=True),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_device_credentials_token_hash", "device_credentials", ["token_hash"])

    op.create_table(
        "pairing_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=True),
        sa.Column("adapter_type", sa.String(48), nullable=False),
        sa.Column("pairing_code_hash", sa.String(64), nullable=False),
        sa.Column("qr_token_hash", sa.String(64), nullable=False),
        sa.Column("challenge_nonce", sa.String(64), nullable=False),
        sa.Column("requested_permissions", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("initiated_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pairing_sessions_device_id", "pairing_sessions", ["device_id"])
    op.create_index("ix_pairing_sessions_pairing_code_hash", "pairing_sessions", ["pairing_code_hash"])
    op.create_index("ix_pairing_sessions_qr_token_hash", "pairing_sessions", ["qr_token_hash"])
    op.create_index("ix_pairing_sessions_status", "pairing_sessions", ["status"])

    op.create_table(
        "device_capabilities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("capability_key", sa.String(80), nullable=False),
        sa.Column("category", sa.String(48), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(256), nullable=True),
        sa.Column("supported", sa.Boolean(), nullable=False),
        sa.Column("unavailable_reason", sa.String(256), nullable=True),
        sa.Column("requires_permission", sa.String(64), nullable=False),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False),
        sa.Column("parameters_schema_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "capability_key", name="uq_device_capability_key"),
    )
    op.create_index("ix_device_capabilities_device_id", "device_capabilities", ["device_id"])

    op.create_table(
        "device_permissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("permission_scope", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("granted_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(256), nullable=True),
        sa.UniqueConstraint("device_id", "permission_scope", name="uq_device_permission_scope"),
    )
    op.create_index("ix_device_permissions_device_id", "device_permissions", ["device_id"])
    op.create_index("ix_device_permissions_permission_scope", "device_permissions", ["permission_scope"])

    op.create_table(
        "command_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("initiated_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("automation_run_id", sa.String(36), nullable=True),
        sa.Column("capability_key", sa.String(80), nullable=False),
        sa.Column("parameters_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("verification_method", sa.String(64), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(512), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("timeout_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_command_executions_device_id", "command_executions", ["device_id"])
    op.create_index("ix_command_executions_automation_run_id", "command_executions", ["automation_run_id"])
    op.create_index("ix_command_executions_status", "command_executions", ["status"])
    op.create_index("ix_command_executions_created_at", "command_executions", ["created_at"])

    op.create_table(
        "communication_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(48), nullable=False),
        sa.Column("access_level", sa.String(32), nullable=False),
        sa.Column("app_package", sa.String(120), nullable=True),
        sa.Column("app_name", sa.String(80), nullable=False),
        sa.Column("sender", sa.String(160), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("body_preview", sa.Text(), nullable=False),
        sa.Column("can_reply", sa.Boolean(), nullable=False),
        sa.Column("reply_action_id", sa.String(128), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("dismissed", sa.Boolean(), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_communication_items_device_id", "communication_items", ["device_id"])
    op.create_index("ix_communication_items_source_type", "communication_items", ["source_type"])
    op.create_index("ix_communication_items_external_id", "communication_items", ["external_id"])
    op.create_index("ix_communication_items_received_at", "communication_items", ["received_at"])

    op.create_table(
        "media_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_type", sa.String(24), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("remote_path", sa.String(1024), nullable=False),
        sa.Column("mime_type", sa.String(96), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_media_items_device_id", "media_items", ["device_id"])
    op.create_index("ix_media_items_media_type", "media_items", ["media_type"])

    op.create_table(
        "active_media_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("app_name", sa.String(80), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("artist", sa.String(160), nullable=True),
        sa.Column("album", sa.String(160), nullable=True),
        sa.Column("playback_state", sa.String(24), nullable=False),
        sa.Column("position_ms", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("volume_percent", sa.Integer(), nullable=True),
        sa.Column("is_muted", sa.Boolean(), nullable=False),
        sa.Column("can_seek", sa.Boolean(), nullable=False),
        sa.Column("can_skip", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "file_transfers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("direction", sa.String(32), nullable=False),
        sa.Column("source_device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_path", sa.String(1024), nullable=False),
        sa.Column("target_path", sa.String(1024), nullable=False),
        sa.Column("staged_local_path", sa.String(1024), nullable=True),
        sa.Column("file_name", sa.String(256), nullable=False),
        sa.Column("mime_type", sa.String(96), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("transferred_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256_checksum", sa.String(64), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_message", sa.String(512), nullable=True),
        sa.Column("authorized_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_file_transfers_source_device_id", "file_transfers", ["source_device_id"])
    op.create_index("ix_file_transfers_target_device_id", "file_transfers", ["target_device_id"])
    op.create_index("ix_file_transfers_status", "file_transfers", ["status"])

    op.create_table(
        "automation_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("trigger_config_json", sa.JSON(), nullable=False),
        sa.Column("conditions_json", sa.JSON(), nullable=False),
        sa.Column("actions_json", sa.JSON(), nullable=False),
        sa.Column("retry_policy_json", sa.JSON(), nullable=False),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_automation_rules_enabled", "automation_rules", ["enabled"])
    op.create_index("ix_automation_rules_trigger_type", "automation_rules", ["trigger_type"])

    op.create_table(
        "automation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("automation_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger_reason", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("step_results_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.String(512), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_automation_runs_rule_id", "automation_runs", ["rule_id"])
    op.create_index("ix_automation_runs_status", "automation_runs", ["status"])

    op.create_table(
        "integration_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("integration_type", sa.String(48), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("endpoint_url", sa.String(256), nullable=False),
        sa.Column("encrypted_credentials", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("health_message", sa.String(256), nullable=True),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_integration_connections_integration_type", "integration_connections", ["integration_type"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=True),
        sa.Column("device_id", sa.String(36), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_severity", "audit_logs", ["severity"])
    op.create_index("ix_audit_logs_category", "audit_logs", ["category"])
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])
    op.create_index("ix_audit_logs_device_id", "audit_logs", ["device_id"])
    op.create_index("ix_audit_logs_category_created", "audit_logs", ["category", "created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("integration_connections")
    op.drop_table("automation_runs")
    op.drop_table("automation_rules")
    op.drop_table("file_transfers")
    op.drop_table("active_media_sessions")
    op.drop_table("media_items")
    op.drop_table("communication_items")
    op.drop_table("command_executions")
    op.drop_table("device_permissions")
    op.drop_table("device_capabilities")
    op.drop_table("pairing_sessions")
    op.drop_table("device_credentials")
    op.drop_table("devices")
    op.drop_table("rooms")
    op.drop_table("user_sessions")
    op.drop_table("users")
