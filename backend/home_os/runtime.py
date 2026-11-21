from pathlib import Path
from home_os.adapters.agent_ws import CompanionAgentHub, PlatformAgentAdapter
from home_os.adapters.android_adb import AndroidAdbAdapter
from home_os.adapters.base import AdapterRegistry
from home_os.adapters.generic_network import GenericNetworkDeviceAdapter
from home_os.adapters.google_tv import AndroidTvRemoteAdapter
from home_os.adapters.home_assistant import HomeAssistantAdapter
from home_os.adapters.ios_native import IosNativeAdapter
from home_os.adapters.local_host import LocalHostAdapter
from home_os.adapters.matter import MatterAdapter
from home_os.config import Settings
from home_os.db.models import Base
from home_os.db.session import create_db_engine_and_session
from home_os.security.crypto import CredentialVault, ensure_local_tls_certificate
from home_os.security.rate_limit import SlidingWindowRateLimiter
from home_os.security.replay import ReplayGuard
from home_os.services.automation_engine import AutomationEngine
from home_os.services.command_orchestrator import CommandOrchestrator
from home_os.services.communications import CommunicationsService
from home_os.services.discovery import DiscoveryService
from home_os.services.event_bus import EventBus
from home_os.services.media_files import MediaAndFileService
from home_os.services.pairing import PairingService


class HomeOsRuntime:
    """Centralized modular-monolith service container."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine, self.SessionLocal = create_db_engine_and_session(settings.resolved_database_url)
        Base.metadata.create_all(bind=self.engine)

        self.vault = CredentialVault(settings.vault_key_path)
        self.tls_info = ensure_local_tls_certificate(settings.tls_dir)
        self.replay_guard = ReplayGuard(window_seconds=settings.replay_window_seconds)
        self.rate_limiter = SlidingWindowRateLimiter()
        self.event_bus = EventBus()

        # Adapter Registry
        self.agent_hub = CompanionAgentHub()
        self.adapter_registry = AdapterRegistry()
        self.adapter_registry.register(GenericNetworkDeviceAdapter(session_factory=self.SessionLocal))
        self.adapter_registry.register(LocalHostAdapter())
        self.adapter_registry.register(PlatformAgentAdapter("android_companion", "smartphone", "Android", self.agent_hub))
        self.adapter_registry.register(PlatformAgentAdapter("ios_companion", "smartphone", "iOS", self.agent_hub))
        self.adapter_registry.register(PlatformAgentAdapter("linux_agent", "desktop", "Linux", self.agent_hub))
        self.adapter_registry.register(PlatformAgentAdapter("windows_agent", "desktop", "Windows", self.agent_hub))
        self.adapter_registry.register(PlatformAgentAdapter("macos_agent", "laptop", "macOS", self.agent_hub))

        self.google_tv_adapter = AndroidTvRemoteAdapter(client_cert_info=self.tls_info)
        self.home_assistant_adapter = HomeAssistantAdapter()
        self.matter_adapter = MatterAdapter()
        self.android_adb_adapter = AndroidAdbAdapter(session_factory=self.SessionLocal, data_dir=settings.data_dir)
        self.ios_native_adapter = IosNativeAdapter(session_factory=self.SessionLocal)

        self.adapter_registry.register(self.google_tv_adapter)
        self.adapter_registry.register(self.home_assistant_adapter)
        self.adapter_registry.register(self.matter_adapter)
        self.adapter_registry.register(self.android_adb_adapter)
        self.adapter_registry.register(self.ios_native_adapter)

        # Core Services
        self.discovery = DiscoveryService(event_bus=self.event_bus)
        self.pairing = PairingService(
            vault=self.vault,
            event_bus=self.event_bus,
            tls_fingerprint=self.tls_info.sha256_fingerprint,
            ttl_seconds=settings.pairing_ttl_seconds,
        )
        self.orchestrator = CommandOrchestrator(
            adapter_registry=self.adapter_registry,
            event_bus=self.event_bus,
        )
        self.communications = CommunicationsService(
            orchestrator=self.orchestrator,
            event_bus=self.event_bus,
        )
        self.media_files = MediaAndFileService(
            orchestrator=self.orchestrator,
            event_bus=self.event_bus,
            staging_dir=settings.transfers_dir,
        )
        self.automations = AutomationEngine(
            orchestrator=self.orchestrator,
            event_bus=self.event_bus,
        )
