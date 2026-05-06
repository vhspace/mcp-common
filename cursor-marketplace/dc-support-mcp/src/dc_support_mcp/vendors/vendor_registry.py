"""
Vendor Registry - Central management for vendor support portal handlers.

This registry allows dynamic registration and retrieval of vendor handlers,
making it easy to add new vendors without modifying core server code.
"""

import os
import sys

from ..validation import ValidationError
from ..vendor_handler import VendorHandler


class VendorRegistry:
    """
    Central registry for managing vendor support portal handlers.

    Vendors can be registered at runtime, and the registry provides
    methods to retrieve configured handlers with proper credentials.
    """

    def __init__(self, verbose: bool = False):
        """
        Initialize the vendor registry.

        Args:
            verbose: Enable verbose logging
        """
        self._handlers: dict[str, type[VendorHandler]] = {}
        self._instances: dict[str, VendorHandler] = {}
        self._verbose = verbose

    def register(self, vendor_name: str, handler_class: type[VendorHandler]) -> None:
        """
        Register a vendor handler class.

        Args:
            vendor_name: Unique vendor identifier (e.g., "ori", "iren")
            handler_class: Handler class (must inherit from VendorHandler)
        """
        if not issubclass(handler_class, VendorHandler):
            raise ValueError("Handler class must inherit from VendorHandler")

        self._handlers[vendor_name.lower()] = handler_class

        if self._verbose:
            sys.stderr.write(f"✓ Registered vendor handler: {vendor_name}\n")

    def get_handler(self, vendor_name: str) -> VendorHandler | None:
        """
        Get or create a vendor handler instance.

        This method lazily initializes handlers on first access,
        reading credentials from environment variables.

        Args:
            vendor_name: Vendor identifier

        Returns:
            Initialized vendor handler or None if not registered/configured

        Raises:
            ValidationError: If vendor is not registered or missing credentials
        """
        vendor_key = vendor_name.lower()

        # Check if handler class is registered
        if vendor_key not in self._handlers:
            raise ValidationError(
                f"Vendor '{vendor_name}' not registered. "
                f"Available vendors: {', '.join(self.list_vendors())}"
            )

        # Return cached instance if exists
        if vendor_key in self._instances:
            return self._instances[vendor_key]

        # Create new instance with credentials from environment
        handler_class = self._handlers[vendor_key]

        try:
            handler = self._create_handler_instance(vendor_key, handler_class)
            self._instances[vendor_key] = handler
            return handler

        except Exception as e:
            raise ValidationError(f"Failed to initialize {vendor_name} handler: {e}") from e

    def _create_handler_instance(
        self, vendor_key: str, handler_class: type[VendorHandler]
    ) -> VendorHandler:
        """
        Create a handler instance with credentials from environment.

        Args:
            vendor_key: Vendor identifier (lowercase)
            handler_class: Handler class to instantiate

        Returns:
            Initialized handler instance

        Raises:
            ValueError: If required credentials are missing
        """
        # Environment variable pattern: {VENDOR}_PORTAL_USERNAME, {VENDOR}_PORTAL_PASSWORD
        env_prefix = vendor_key.upper()
        username = os.getenv(f"{env_prefix}_PORTAL_USERNAME")
        password = os.getenv(f"{env_prefix}_PORTAL_PASSWORD")

        if not username or not password:
            raise ValueError(
                f"Missing credentials for {vendor_key}. "
                f"Set {env_prefix}_PORTAL_USERNAME and {env_prefix}_PORTAL_PASSWORD"
            )

        # Create handler instance
        # Most handlers accept (username, password, verbose) in __init__
        handler = handler_class(email=username, password=password, verbose=self._verbose)  # type: ignore[call-arg]

        if self._verbose:
            sys.stderr.write(f"✓ Initialized {vendor_key} handler\n")

        return handler

    def list_vendors(self) -> list[str]:
        """
        Get list of registered vendor names.

        Returns:
            List of vendor identifiers
        """
        return sorted(self._handlers.keys())

    def is_vendor_registered(self, vendor_name: str) -> bool:
        """
        Check if a vendor is registered.

        Args:
            vendor_name: Vendor identifier

        Returns:
            True if vendor is registered
        """
        return vendor_name.lower() in self._handlers

    def clear_cache(self, vendor_name: str | None = None) -> None:
        """
        Clear cached handler instances, closing any held resources.

        Useful for forcing re-authentication or resetting connections.

        Args:
            vendor_name: Specific vendor to clear, or None to clear all
        """
        if vendor_name:
            vendor_key = vendor_name.lower()
            if vendor_key in self._instances:
                self._instances[vendor_key].close()
                del self._instances[vendor_key]
                if self._verbose:
                    sys.stderr.write(f"✓ Cleared cache for {vendor_name}\n")
        else:
            for handler in self._instances.values():
                handler.close()
            self._instances.clear()
            if self._verbose:
                sys.stderr.write("✓ Cleared all vendor handler caches\n")
