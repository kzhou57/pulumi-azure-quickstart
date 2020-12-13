"""An Azure Python Pulumi program"""

import pulumi
from pulumi_azure import core, storage
from pulumi_azure.core import ResourceGroup
from pulumi_azure.storage import Account
from pulumi_azuread import Application, ServicePrincipal, ServicePrincipalPassword
from pulumi_azure.role import Assignment
from pulumi_azure.containerservice import KubernetesCluster, Registry
from pulumi_azure.network import VirtualNetwork, Subnet
from pulumi_kubernetes import Provider
from pulumi_kubernetes.apps.v1 import Deployment
from pulumi_kubernetes.core.v1 import Service, Namespace

config = pulumi.Config()
PASSWORD = config.require('password')
SSHKEY = config.require('sshPublicKey')
LOCATION = config.get('location') or 'east us'

app = Application(
    'kzhou-app',
    name= 'kzhou-app'
)

sp = ServicePrincipal(
    'kzhou-aks-sp',
    application_id=app.application_id
)

sppwd = ServicePrincipalPassword(
    'kzhou-aks-sp-pwd',
    service_principal_id=sp.id,
    end_date='2025-01-01T01:02:03Z',
    value=PASSWORD
)

rg = ResourceGroup('rg', name = 'kzhou-rg', location=LOCATION)

# Create an Azure resource (Storage Account)
account = Account('storage',
                          # The location for the storage account will be derived automatically from the resource group.
                          resource_group_name=rg.name,
                          account_tier='Standard',
                          account_replication_type='LRS')


vnet = VirtualNetwork(
    'vnet',
    name= 'kzhou-vnet',
    location=rg.location,
    resource_group_name=rg.name,
    address_spaces=['10.0.0.0/16']
)
subnet = Subnet(
    'subnet',
    name='kzhou-subnet',
    resource_group_name=rg.name,
    address_prefix='10.0.0.0/24',
    virtual_network_name=vnet.name
)

# create Azure Container Registry to store images in
acr = Registry(
    'acr',
    name='kzhouacr',
    location=rg.location,
    resource_group_name=rg.name,
    sku="basic"
)

acr_assignment = Assignment(
    'acr-permissions',
    principal_id=sp.id,
    role_definition_name='AcrPull',
    scope=acr.id
)

subnet_assignment = Assignment(
    'subnet-permissions',
    principal_id=sp.id,
    role_definition_name='Network Contributor',
    scope=subnet.id
)

aks = KubernetesCluster(
    'aks',
    name='kzhou-aks',
    location=rg.location,
    resource_group_name=rg.name,
    kubernetes_version="1.13.5",
    dns_prefix="dns",
    agent_pool_profile=(
        {
            "name": "type1",
            "count": 2,
            "vmSize": "Standard_B2ms",
            "osType": "Linux",
            "maxPods": 110,
            "vnet_subnet_id": subnet.id
        }
    ),
    linux_profile=(
        {
            "adminUsername": "azureuser",
            "ssh_key": {
                "keyData": SSHKEY
            }
        }
    ),
    service_principal={
        "clientId": app.application_id,
        "clientSecret": sppwd.value
    },
    role_based_access_control={
        "enabled": "true"
    },
    network_profile=(
        {
            "networkPlugin": "azure",
            "serviceCidr": "10.10.0.0/16",
            "dns_service_ip": "10.10.0.10",
            "dockerBridgeCidr": "172.17.0.1/16"
        }
    ), __opts__=ResourceOptions(depends_on=[acr_assignment, subnet_assignment])
)

custom_provider = Provider(
    "inflation_provider", kubeconfig=aks.kube_config_raw
)

# Export the connection string for the storage account
pulumi.export('storage_connection_string', account.primary_connection_string)
pulumi.export('kubeconfig', aks.kube_config_raw)
