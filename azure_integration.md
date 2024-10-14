# Azure Integration Documentation

## Summary
This document outlines what resources need to provisioned, how they should be provisioned, etc. for Fable to run.

## Table of Contents
- [Azure Key Vault](#azure-key-vault)
    - To store configuration for Fable (which includes secret keys)
- [Azure Storage Account](#azure-storage-account)
    - To store URLs for processing and store output of Fable runs
- [Azure User-Assigned Identity](#azure-user-assigned-identity)
    - To give container groups appropriate permissions to access other Azure resources
- [Azure Container Registry](#azure-container-registry)
    - To store Docker container for Fable
- [Azure Container Deploy](#azure-container-deploy)
    - To run Fable

## Prerequisites 
- Install Azure CLI: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli and authenticate with `az login`
- Create a resource group. All Azure resources should use this resource group
    ```
    RESOURCE_GROUP={Enter resource group name}
    az group create --name $RESOURCE_GROUP --location $REGION
    ```

## Azure Key Vault
This key vault will store the configuration for Fable as well as the permissions necessary for the final VMs to be able to access all other Azure resources.

Setting this Key Vault up will roughly follow this [tutorial](https://docs.microsoft.com/en-us/azure/container-instances/container-instances-managed-identity#create-an-azure-key-vault)

1. Create the Key Vault
```
KEY_VAULT_NAME={Enter key vault name}
REGION=eastus
RESOURCE_GROUP={Enter resource group name}
az keyvault create --name $KEY_VAULT_NAME --resource-group $RESOURCE_GROUP --location $REGION
```
1. Navigate to the console and verify that Key Vault was created successfully
1. Navigate to the Access Policies tab within the Key Vault console and enable access to the following and press "Save" to commit changes.
    - Azure Virtual Machines for deployment
    - Azure Resource Manager for template deployment
    - Azure Disk Encryption for volume encryption
    
## Azure Storage Account
A storage account will be used for both Azure Queues and Azure File Shares

### Creating Storage Account
1. Navigate to Azure Portal -> Storage Accounts -> Click on New
1. Set resource group to the one you created, name the account, and leave all other default options as is
1. Verify that storage account was successfully created

### Create Azure Queue
1. Within the Storage Account console, navigate to the Queues tab
1. Create a New Queue with default settings

### Create Azure Files
1. Within the Storage Account console, navigate to the Queues tab
1. Create a New File Share with default settings

### Integrate with Azure Key Vault
The Python program that adds messages gets the queue connection string and name from the Key Vault, so we need to populate these values

1. Navigate to the Storage Account console -> Access Keys
1. Show keys and copy the first connection string (we will need this value for later)
1. Navigate to the Key Vault console -> Click on Generate/Import
1. Set the name of the secret to `storage-connection-str` and value to the copied connection string and create the secret
1. Create another secret with the name `queue-name` and value to the name of your queue
1. Create another secret with the name `fileshare-name` and value to the name of your file share
1. Create another secret with the name `fable-config` and value to a stringified version of the config file (reach out to Jingyuan (jingyz@umich.edu) for more information)

### Sending Messages to Azure Queues
Note: This can be run locally (does not need to be within container)
Note: May need virtual environment
1. Set env variable FABLE_CONFIG_VAULTNAME: `export FABLE_CONFIG_VAULTNAME={Enter Vault Name HERE}`
2. Authenticate with Azure: `az login`
3. Run program: `python3 azure_send_messages.py`
    - Relies on cluster.json file (sent via Google Drive/Gmail) which should be structured like
        ```
        {
            "uoregon.edu": [
                "http://comm.uoregon.edu/newsreleases/2004/20040430B.html",
                "https://jsma.uoregon.edu/exhibitions/ten-symbols-longevity-and-late-joseon-korean-culture",
                "http://oceanlaw.uoregon.edu/publications/new_carissa.html",
                "http://darkwing.uoregon.edu/~sergiok/brasil/belo.html"
            ],
            hostname: [
                URLs
            ]
            ...
        }
        ```
    - Currently ignores any hostnames with more than 150 URLs
    - Time-to-live of messages is currently set to infinity

## Azure User-Assigned Identity
We need an identity so that our containers in the future have access to the Azure Key Vault, Queue, and File Share. This roughly follows this [tutorial](https://docs.microsoft.com/en-us/azure/container-instances/container-instances-managed-identity)

1. Create an identity
    ```
    RESOURCE_GROUP={Enter Resource Group Name here}
    IDENTITY_NAME={Enter Identity Name here}
    az identity create \
        --resource-group $RESOURCE_GROUP \
        --name $IDENTITY_NAME
    ```
1. Save the following information into env variables (needed for later)
    ```
    # Get service principal ID of the user-assigned identity
    spID=$(az identity show \
        --resource-group $RESOURCE_GROUP \
        --name $IDENTITY_NAME \
        --query principalId --output tsv)

    # Get resource ID of the user-assigned identity
    resourceID=$(az identity show \
        --resource-group $RESOURCE_GROUP \
        --name $IDENTITY_NAME \
        --query id --output tsv)
    ```
1. Run the following az keyvault set-policy command to set an access policy on the key vault to get secrets from the key vault:
    ```
    KEY_VAULT_NAME={Enter Key Vault name here}
    az keyvault set-policy \
        --name $KEY_VAULT_NAME \
        --resource-group $RESOURCE_GROUP \
        --object-id $spID \
        --secret-permissions get
    ```

1. Navigate to the Key Vault console -> Access policies and verify that there is an Application policy with the identity name

## Azure Container Registry
We need a registry to house our Docker containers. 

1. Navigate to the Azure Container Registry console and create a registry under your resource group
1. Run the following to be able to push and pull from the Container Registry
    ```
    KEY_VAULT_NAME={Enter Key Vault name}
    ACR_NAME={Enter Container Registry name}
    az keyvault secret set \
        --vault-name $KEY_VAULT_NAME \
        --name $ACR_NAME-pull-pwd \
        --value $(az ad sp create-for-rbac \
                        --name http://$ACR_NAME-pull \
                        --scopes $(az acr show --name $ACR_NAME --query id --output tsv) \
                        --role acrpull \
                        --query password \
                        --output tsv)

    # Store service principal ID in vault (the registry *username*)
    az keyvault secret set \
        --vault-name $KEY_VAULT_NAME \
        --name $ACR_NAME-pull-usr \
        --value $(az ad sp show --id http://$ACR_NAME-pull --query appId --output tsv)
    ```

## Azure Container Deploy
1. Authenticate with Azure: `az login`
1. Authenticate with Azure container repository
    ```
    ACR_NAME={Enter Container Registry name}
    az acr login --name $ACR_NAME
    ```
1. Build Docker container: `docker build -t fable .`
1. Tag container: `docker tag fable $ACR_NAME.azurecr.io/fable:v1`
1. Push container to Azure repository: `docker push $ACR_NAME.azurecr.io/fable:v1`
    - Note: Container repository permissions should already be set up to allow for running instances
1. Set environment variables:
    ```
    RESOURCE_GROUP={Enter Resource Group name}
    ACR_NAME={Enter Container Registry name}
    AKV_NAME={Enter Key Vault name}
    ACR_LOGIN_SERVER=$ACR_NAME.azurecr.io
    IDENTITY_NAME={Enter Identity name}
    resourceID=$(az identity show --resource-group $RESOURCE_GROUP --name $IDENTITY_NAME --query id --output tsv)
    ```
1. Deploy the container instance:
    ```
    az container create \
    --resource-group $RESOURCE_GROUP \
    --name fable \
    --image $ACR_LOGIN_SERVER/fable:v1 \
    --registry-login-server $ACR_LOGIN_SERVER \
    --registry-username $(az keyvault secret show --vault-name $AKV_NAME -n $ACR_NAME-pull-usr --query value -o tsv) \
    --registry-password $(az keyvault secret show --vault-name $AKV_NAME -n $ACR_NAME-pull-pwd --query value -o tsv) \
    --assign-identity $resourceID \
    --environment-variables "'FABLE_CONFIG_KEYVAULT'=1 'FABLE_CONFIG_VAULTNAME'=$KEY_VAULT_NAME 'FABLE_CONFIG_SECRETNAME'='fable-config' 'ROOT_USER'=1" \
    --cpu 2 \
    --memory 4 \
    --command-line "tail -f /dev/null"
    ```
    - Note: With this command, you will be deploying an instance with 2 CPU cores and 4 GB of RAM. Change these parameters if necessary
1. Once deployed (takes a few minutes), launch Bash shell: `az container exec --resource-group achitta-broken-detection_group --name fable --exec-command "/bin/bash"`
1. Launch tmux: `tmux`
1. Authenticate with Azure: `az login` and follow prompts (this is likely not necessary as we are using a managed identity)
1. Run python program: `python3 fable_azure.py`
1. Once running (sometimes takes a few tries; wait until Fable output starts coming to console), detach from tmux: `ctrl+b d`
1. Exit from terminal
1. Re-attach to tmux: `tmux a -t 0` (or whatever the session number is)
