""" Azure client wrapper to access queue service, file shares, and secrets"""

from azure.storage.queue import (
        QueueClient
)
from azure.storage.fileshare import ShareFileClient
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential
import os
import json

class AzureClient:
    def __init__(self):
        vault_name = os.getenv('FABLE_CONFIG_VAULTNAME')
        self.connect_str = self.azure_kv(vault_name, "storage-connection-str").value
        self.queue_name = self.azure_kv(vault_name, "queue-name").value
        self.fileshare_name = self.azure_kv(vault_name, "fileshare-name").value
        self.queue_client = QueueClient.from_connection_string(self.connect_str, self.queue_name)

    def get_queue_length(self) -> int:
        """Get queue length"""
        return self.queue_client.get_queue_properties().approximate_message_count

    def poll_message(self) -> dict:
        """Get message from queue."""
        try:
            message = self.queue_client.receive_message()
            self.queue_client.delete_message(message.id, message.pop_receipt)
            urlInfo = json.loads(message.content)
            return urlInfo
        except:
            return None
    
    def send_message(self, messageDict):
        """Send JSON message given python dictionary"""
        self.queue_client.send_message(json.dumps(messageDict), time_to_live=-1)

    def upload_file(self, srcPathLocal, dstPathAzure):
        """Upload local file to Azure files"""
        file_client = ShareFileClient.from_connection_string(conn_str=self.connect_str, 
        share_name=self.fileshare_name, file_path=dstPathAzure)
        
        with open(srcPathLocal, "rb") as source_file:
            file_client.upload_file(source_file)
        
        file_client.close()

    def azure_kv(self, vault_name, secret_name):
        # * Details: https://docs.microsoft.com/en-us/azure/key-vault/secrets/quick-create-python?tabs=cmd
        KVUri = f"https://{vault_name}.vault.azure.net"

        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=KVUri, credential=credential)
        retrieved_secret = client.get_secret(secret_name)
        return retrieved_secret