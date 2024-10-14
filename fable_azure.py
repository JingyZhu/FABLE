""" Run Fable using Azure services """
import logging
from fable import ReorgPageFinder
from azure_client import AzureClient

rpf = ReorgPageFinder(classname='achitta', logname='achitta', loglevel=logging.DEBUG)
azureClient = AzureClient()

def pkill(pattern):
    try:
        subprocess.run(["pkill", "-f", pattern], check=False)
    except:
        pass

# Run Fable and upload logs on success to Azure files
def fable_api(urlInfo: dict):
    hostname = urlInfo['hostname']
    urls = urlInfo['urls']
    try:
        rpf.init_site(hostname, urls)
        rpf.search(required_urls=urls)
        rpf.discover(required_urls=urls)
    except:
        pass
    srcLogPath = f"logs/{hostname}.log"
    dstLogPath = f"logs/{hostname}.log"
    azureClient.upload_file(srcLogPath, dstLogPath)

# Read URLs from Azure Queues and run Fable on them    
def main():
    count = 0
    with open('progress.txt', 'a') as progress_file:
        while azureClient.get_queue_length() > 0:
            try:
                # Kill any stale chrome processes in case of memory issues
                pkill('chrome')

                urlInfo = azureClient.poll_message()
                progress_file.write(f"Processing number {count}\tHostname: {urlInfo['hostname']}\n")
                fable_api(urlInfo)
                count += 1
            except:
                pass

if __name__ == "__main__":
    main()
