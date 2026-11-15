#!/usr/bin/env python3
"""
Vault Authentication Helper for IaC Services
Manages AppRole authentication and secret retrieval
"""

import os
import sys
import hvac
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/iac/vault_helper.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class VaultClient:
    """
    Manages authentication and secret retrieval from Vault
    
    Expected environment variables:
    - VAULT_ADDR: Vault server address (e.g., https://vault.internal:8200)
    - VAULT_ROLE_ID: AppRole role ID
    - VAULT_ROLE_SECRET_ID: AppRole secret ID
    - VAULT_SKIP_VERIFY: Skip TLS verification (for dev/testing only)
    """
    
    def __init__(self, vault_addr: Optional[str] = None, 
                 role_id: Optional[str] = None,
                 secret_id: Optional[str] = None,
                 skip_verify: bool = False):
        """
        Initialize Vault client
        
        Args:
            vault_addr: Vault server address (uses env var if not provided)
            role_id: AppRole role ID (uses env var if not provided)
            secret_id: AppRole secret ID (uses env var if not provided)
            skip_verify: Skip TLS certificate verification
        """
        self.vault_addr = vault_addr or os.getenv("VAULT_ADDR", "http://localhost:8200")
        self.role_id = role_id or os.getenv("VAULT_ROLE_ID")
        self.secret_id = secret_id or os.getenv("VAULT_ROLE_SECRET_ID")
        self.skip_verify = skip_verify or os.getenv("VAULT_SKIP_VERIFY", "false").lower() == "true"
        
        # Validate required parameters
        if not all([self.role_id, self.secret_id]):
            raise ValueError(
                "VAULT_ROLE_ID and VAULT_ROLE_SECRET_ID environment variables are required"
            )
        
        # Initialize hvac client
        self.client = hvac.Client(
            url=self.vault_addr,
            verify=not self.skip_verify
        )
        
        self.token = None
        self.token_ttl = None
        
        logger.info(f"VaultClient initialized: {self.vault_addr}")
    
    def authenticate(self) -> Dict[str, Any]:
        """
        Authenticate to Vault using AppRole
        
        Returns:
            Authentication response dict
            
        Raises:
            Exception: If authentication fails
        """
        try:
            logger.info("Authenticating to Vault using AppRole")
            
            auth_response = self.client.auth.approle.login(
                role_id=self.role_id,
                secret_id=self.secret_id
            )
            
            self.token = auth_response['auth']['client_token']
            self.token_ttl = auth_response['auth']['lease_duration']
            
            self.client.token = self.token
            
            logger.info(f"Successfully authenticated to Vault (TTL: {self.token_ttl}s)")
            
            return auth_response
        
        except Exception as e:
            logger.error(f"Vault authentication failed: {e}")
            raise
    
    def is_authenticated(self) -> bool:
        """Check if currently authenticated to Vault"""
        return self.token is not None
    
    def get_secret(self, path: str, field: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieve a secret from Vault KV v2
        
        Args:
            path: Secret path (e.g., "iac/api_token")
            field: Optional specific field to retrieve
            
        Returns:
            Secret data dict or specific field value
            
        Raises:
            Exception: If secret retrieval fails
        """
        if not self.is_authenticated():
            self.authenticate()
        
        try:
            logger.info(f"Retrieving secret: {path}")
            
            response = self.client.secrets.kv.v2.read_secret_version(
                path=path,
                mount_point='secret'
            )
            
            data = response['data']['data']
            
            if field:
                if field not in data:
                    raise KeyError(f"Field '{field}' not found in secret")
                return data[field]
            
            logger.info(f"Successfully retrieved secret: {path}")
            return data
        
        except Exception as e:
            logger.error(f"Failed to retrieve secret {path}: {e}")
            raise
    
    def put_secret(self, path: str, data: Dict[str, Any], 
                  merge: bool = False) -> Dict[str, Any]:
        """
        Store or update a secret in Vault KV v2
        
        Args:
            path: Secret path (e.g., "iac/api_token")
            data: Secret data dict
            merge: Merge with existing data if True, replace if False
            
        Returns:
            Response dict
            
        Raises:
            Exception: If secret storage fails
        """
        if not self.is_authenticated():
            self.authenticate()
        
        try:
            logger.info(f"Storing secret: {path}")
            
            # Optionally merge with existing
            if merge:
                try:
                    existing = self.get_secret(path)
                    data = {**existing, **data}
                except Exception:
                    pass  # Secret doesn't exist yet
            
            response = self.client.secrets.kv.v2.create_or_update_secret_version(
                path=path,
                secret_data=data,
                mount_point='secret'
            )
            
            logger.info(f"Successfully stored secret: {path}")
            return response
        
        except Exception as e:
            logger.error(f"Failed to store secret {path}: {e}")
            raise
    
    def list_secrets(self, path: str) -> list:
        """
        List secrets at a given path
        
        Args:
            path: Secret path prefix (e.g., "iac")
            
        Returns:
            List of secret names
        """
        if not self.is_authenticated():
            self.authenticate()
        
        try:
            logger.info(f"Listing secrets at: {path}")
            
            response = self.client.secrets.kv.v2.list_secrets(
                path=path,
                mount_point='secret'
            )
            
            keys = response['data']['keys']
            logger.info(f"Found {len(keys)} secrets at {path}")
            
            return keys
        
        except Exception as e:
            logger.error(f"Failed to list secrets at {path}: {e}")
            return []
    
    def delete_secret(self, path: str) -> bool:
        """
        Delete a secret from Vault
        
        Args:
            path: Secret path
            
        Returns:
            True if successful
        """
        if not self.is_authenticated():
            self.authenticate()
        
        try:
            logger.info(f"Deleting secret: {path}")
            
            self.client.secrets.kv.v2.delete_secret_version(
                path=path,
                mount_point='secret'
            )
            
            logger.info(f"Successfully deleted secret: {path}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to delete secret {path}: {e}")
            return False


def get_vault_client(
    vault_addr: Optional[str] = None,
    role_id: Optional[str] = None,
    secret_id: Optional[str] = None
) -> VaultClient:
    """
    Factory function to create and authenticate a Vault client
    
    Returns:
        Authenticated VaultClient instance
    """
    client = VaultClient(vault_addr=vault_addr, role_id=role_id, secret_id=secret_id)
    client.authenticate()
    return client


if __name__ == "__main__":
    # Example usage
    try:
        # Load environment
        if Path('/opt/vault/.env').exists():
            load_dotenv('/opt/vault/.env')
        
        # Create client
        client = get_vault_client()
        
        # Test retrieval
        secret = client.get_secret('iac/api_token')
        print(f"API Token: {secret.get('token', 'NOT FOUND')}")
        
        # List available secrets
        secrets = client.list_secrets('iac')
        print(f"Available secrets: {secrets}")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
