from azure_client import AzureClient
import json

azureClient = AzureClient()

def main():
    queue_client = AzureClient()
    clusters = json.load(open('clusters.json'))
    for hostname, urls in clusters.items():
        try:
            # Ignore hostnames that have more than 150 URLs 
            if len(urls) > 150:
                continue

            queueObj = {
                'hostname': hostname,
                'urls': urls
            }
            queue_client.send_message(queueObj)
        except:
            print("Failed: " + hostname)
            pass

if __name__ == "__main__":
    main()