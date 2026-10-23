"""Authentication manager for ServiceNow MCP server with multi-strategy support.

This module provides a unified authentication interface for ServiceNow API access,
supporting three authentication strategies:

1. Basic Authentication: Username/password encoded in Authorization header
2. OAuth 2.0: Client credentials or password grant flows
3. API Key: Custom header-based authentication

Authentication Strategy Selection:
- Use BASIC for simple username/password scenarios
- Use OAUTH for service-to-service integration (client credentials)
- Use OAUTH + password for user-delegated access
- Use API_KEY for token-based authentication with custom headers

The AuthManager caches tokens and provides automatic header construction,
making it seamless to switch between authentication methods.
"""

import base64
import logging
from typing import Any, Dict, Optional

import requests

from servicenow_mcp.utils.config import AuthConfig, AuthType
from servicenow_mcp.utils.release_context import (
    get_instance_release_context,
    get_module_release_behavior,
)

logger = logging.getLogger(__name__)


class AuthManager:
    """Unified authentication handler for ServiceNow API with multi-strategy support.
    
    Manages credentials and token lifecycle for three authentication methods:
    - Basic Auth: HTTP Basic authentication with username/password
    - OAuth 2.0: OAuth client credentials and password grant flows
    - API Key: Custom header-based authentication with bearer tokens
    
    Token Management:
    - Automatically acquires OAuth tokens on first use (lazy initialization)
    - Caches tokens to avoid unnecessary token endpoint calls
    - Provides refresh_token() for manual token refresh when expired
    - Computes elapsed time for tokens to detect expiration
    
    Release Context:
    - Caches ServiceNow build/release metadata from instance
    - Provides release-aware behavior hints for modules
    - Useful for handling API differences across ServiceNow versions
    """

    def __init__(self, config: AuthConfig, instance_url: str = None):
        """Initialize authentication manager with strategy and credentials.
        
        Sets up the authentication handler with the specified strategy (Basic/OAuth/API Key).
        Defers token acquisition to first use (lazy initialization) for efficiency.
        Instance URL can be provided upfront or derived from OAuth token endpoint.
        
        Args:
            config: AuthConfig specifying authentication strategy and credentials
                - type: AuthType.BASIC, .OAUTH, or .API_KEY
                - basic: BasicAuthConfig with username/password (for Basic auth)
                - oauth: OAuthConfig with client credentials (for OAuth)
                - api_key: ApiKeyConfig with header name and key (for API key auth)
            instance_url: ServiceNow instance URL (e.g., https://myinstance.service-now.com)
                - Required for OAuth if token_url not specified
                - Optional, can be derived from OAuth token endpoint
        
        Side Effects:
            - Sets token and token_type to None (acquired on first get_headers() call)
            - Initializes release_context and release_behavior to None
        """
        self.config = config
        self.instance_url = instance_url
        self.token: Optional[str] = None
        self.token_type: Optional[str] = None
        self.release_context: Optional[Dict[str, Any]] = None
        self.release_behavior: Optional[Dict[str, Any]] = None

    def get_headers(self) -> Dict[str, str]:
        """Get authentication headers for ServiceNow API requests.
        
        Constructs HTTP headers with appropriate authentication based on configured
        strategy. Adds standard Content-Type and Accept headers for all requests,
        then adds auth-specific headers based on strategy.
        
        Authentication Methods:
        1. BASIC: Encodes username:password and adds Authorization: Basic header
        2. OAUTH: Acquires token (if needed) and adds Authorization: Bearer header
        3. API_KEY: Adds custom header with API key (header name from config)
        
        Returns:
            Dict with headers to include in all API requests:
            - Accept: application/json (always present)
            - Content-Type: application/json (always present)
            - Authorization: Varies by authentication method
        
        Raises:
            ValueError: If required auth config is missing for selected strategy
        
        Note:
            OAuth tokens are cached and reused across calls for efficiency.
            Use refresh_token() to explicitly refresh an expired token.
        """
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if self.config.type == AuthType.BASIC:
            if not self.config.basic:
                raise ValueError("Basic auth configuration is required")

            auth_str = f"{self.config.basic.username}:{self.config.basic.password}"
            encoded = base64.b64encode(auth_str.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        elif self.config.type == AuthType.OAUTH:
            if not self.token:
                self._get_oauth_token()

            headers["Authorization"] = f"{self.token_type} {self.token}"

        elif self.config.type == AuthType.API_KEY:
            if not self.config.api_key:
                raise ValueError("API key configuration is required")

            headers[self.config.api_key.header_name] = self.config.api_key.api_key

        return headers

    def _get_oauth_token(self):
        """Acquire OAuth 2.0 access token using configured grant flow.
        
        Attempts two OAuth grant flows in order, falling back if first fails:
        1. Client Credentials Grant: service-to-service auth (recommended)
        2. Password Grant: user-delegated auth (if credentials provided)
        
        Token Acquisition Strategy:
        - Constructs token_url from OAuth config or derives from instance URL
        - Encodes client_id:client_secret in Authorization header (Basic auth to token endpoint)
        - POSTs with grant_type=client_credentials (preferred)
        - Falls back to grant_type=password if client_credentials fails and credentials provided
        - Extracts access_token and token_type from successful response
        - Caches token for reuse across API calls
        
        Token Endpoint URL Derivation:
        - If token_url in config: uses it directly
        - Otherwise: derives from instance_url (https://instance.service-now.com/oauth_token.do)
        
        Args:
            (none - uses self.config and self.instance_url)
        
        Side Effects:
            - Sets self.token and self.token_type for use in Authorization header
            - Logs each grant attempt at INFO level for debugging
        
        Raises:
            ValueError: If OAuth config missing, instance URL invalid, or both grants fail
        
        Note:
            Client credentials grant is recommended for service-to-service.
            Password grant requires oauth_config.username and oauth_config.password.
        """
        if not self.config.oauth:
            raise ValueError("OAuth configuration is required")
        oauth_config = self.config.oauth

        # Determine token URL
        token_url = oauth_config.token_url
        if not token_url:
            if not self.instance_url:
                raise ValueError("Instance URL is required for OAuth authentication")
            instance_parts = self.instance_url.split(".")
            if len(instance_parts) < 2:
                raise ValueError(f"Invalid instance URL: {self.instance_url}")
            instance_name = instance_parts[0].split("//")[-1]
            token_url = f"https://{instance_name}.service-now.com/oauth_token.do"

        # Prepare Authorization header
        auth_str = f"{oauth_config.client_id}:{oauth_config.client_secret}"
        auth_header = base64.b64encode(auth_str.encode()).decode()
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        # Try client_credentials grant first
        data_client_credentials = {"grant_type": "client_credentials"}

        logger.info("Attempting client_credentials grant...")
        response = requests.post(token_url, headers=headers, data=data_client_credentials)

        logger.info(f"client_credentials response status: {response.status_code}")
        logger.info(f"client_credentials response body: {response.text}")

        if response.status_code == 200:
            token_data = response.json()
            self.token = token_data.get("access_token")
            self.token_type = token_data.get("token_type", "Bearer")
            return

        # Try password grant if client_credentials failed
        if oauth_config.username and oauth_config.password:
            data_password = {
                "grant_type": "password",
                "username": oauth_config.username,
                "password": oauth_config.password,
            }

            logger.info("Attempting password grant...")
            response = requests.post(token_url, headers=headers, data=data_password)

            logger.info(f"password grant response status: {response.status_code}")
            logger.info(f"password grant response body: {response.text}")

            if response.status_code == 200:
                token_data = response.json()
                self.token = token_data.get("access_token")
                self.token_type = token_data.get("token_type", "Bearer")
                return

        raise ValueError(
            "Failed to get OAuth token using both client_credentials and password grants."
        )

    def refresh_token(self):
        """Refresh OAuth token by re-acquiring from token endpoint.
        
        Forces re-acquisition of OAuth token by calling _get_oauth_token(),
        useful when token has expired or authentication failed.
        
        Only performs token refresh if OAuth authentication is configured.
        Other auth methods (Basic, API Key) are stateless and don't require refresh.
        
        Side Effects:
            - Sets new self.token and self.token_type
            - Logs OAuth grant attempt at INFO level
        
        Returns:
            None (token stored in self.token, accessible via get_headers())
        
        Raises:
            ValueError: If both OAuth grant flows fail (see _get_oauth_token)
        """
        if self.config.type == AuthType.OAUTH:
            self._get_oauth_token()

    def get_release_context(self, server_config, force_refresh: bool = False) -> Dict[str, Any]:
        """Retrieve ServiceNow instance build/release metadata with caching.
        
        Fetches instance release context (build number, version, plugins, etc.) from
        the ServiceNow instance. Caches result to avoid repeated API calls.
        
        Release Context Usage:
        - Detect ServiceNow version for API compatibility checks
        - Enable version-specific behavior and workarounds
        - Track feature availability across instances
        
        Args:
            server_config: ServerConfig with instance URL and authentication
            force_refresh: If True, bypass cache and re-fetch from instance
        
        Returns:
            Dict with instance release metadata (build, version, plugins, features)
        
        Side Effects:
            - Caches context in self.release_context for future calls
            - Makes HTTP request to instance if cache miss or force_refresh=True
        
        Note:
            Uses get_instance_release_context util function for actual fetching.
        """
        context = get_instance_release_context(
            server_config,
            self,
            force_refresh=force_refresh,
        )
        self.release_context = context
        return context

    def get_release_behavior(
        self,
        server_config,
        module_name: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """Get release-aware behavior configuration for a module.
        
        Retrieves behavior hints specific to the module on the current ServiceNow
        version. Enables modules to adapt behavior (retry logic, timeouts, API calls)
        based on instance build.
        
        Release-Aware Adaptation:
        - Adjust API endpoint behavior for version differences
        - Set appropriate retry counts/delays for specific builds
        - Enable/disable features based on instance version
        - Apply known workarounds for specific build numbers
        
        Args:
            server_config: ServerConfig with instance URL and authentication
            module_name: Name of module to get behavior for (e.g., 'cmdb_tools')
            force_refresh: If True, bypass cache and re-fetch release context
        
        Returns:
            Dict with module-specific behavior hints for current instance version
        
        Side Effects:
            - Calls get_release_context internally (may cache or refresh)
            - Caches behavior in self.release_behavior
        
        Note:
            First calls get_release_context to get instance metadata,
            then calls get_module_release_behavior util to compute module-specific hints.
        """
        context = self.get_release_context(server_config, force_refresh=force_refresh)
        behavior = get_module_release_behavior(module_name, context)
        self.release_behavior = behavior
        return behavior
