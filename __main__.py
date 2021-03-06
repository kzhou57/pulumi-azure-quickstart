"""An Azure Python Pulumi program"""

import pulumi
from pulumi import ResourceOptions
from pulumi_azure import core, storage, mssql
from pulumi_azure.core import ResourceGroup
from pulumi_azure.storage import Account
from pulumi_azuread import Application, ServicePrincipal, ServicePrincipalPassword
from pulumi_azure.authorization import Assignment
from pulumi_azure.containerservice import KubernetesCluster, Registry
from pulumi_azure.network import VirtualNetwork, Subnet
from pulumi_kubernetes import Provider
from pulumi_kubernetes.apps.v1 import Deployment
from pulumi_kubernetes.core.v1 import Service, Namespace

config = pulumi.Config()
PASSWORD = config.require('password')
SSHKEY = config.require('sshPublicKey')
LOCATION = config.get('location') or 'east us'
SA_PASSWORD = config.require('sa_password')

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
    address_prefixes=['10.0.0.0/24'],
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
    scope=acr.id,
    role_definition_name='AcrPull',
    principal_id=sp.id
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
    kubernetes_version="1.19.3",
    dns_prefix="dns",
    default_node_pool=(
        {
            "name": "type1",
            "node_count": 2,
            "vm_size": "Standard_B2ms",
            "max_pods": 110,
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
    "k8s", kubeconfig=aks.kube_config_raw
)

sql = mssql.Server("kzhou-sql",
    resource_group_name=rg.name,
    location=rg.location,
    version="12.0",
    administrator_login="sysadmin",
    administrator_login_password=SA_PASSWORD,
    minimum_tls_version="1.2",
    public_network_access_enabled=True
    )

name = 'kzhou'

# Create a Kubernetes Namespace
namespace = Namespace(name,
    metadata={},
    __opts__=ResourceOptions(provider=custom_provider)
)

# Create a NGINX Deployment
appLabels = { "appClass": name }
deployment = Deployment(name,
            metadata={
                "labels": appLabels
            },
            spec={
                "selector": {
                    "match_labels": appLabels
                },
                "replicas": 1,
                "template": {
                    "metadata": {
                        "labels": appLabels
                    },
                    "spec": {
                        "containers": [
                            {
                                "name": name,
                                "image": "nginx",
                                "ports": [
                                    {
                                        "name": "http",
                                        "containerPort": 80
                                    }
                                ]
                            }
                        ]
                    }
                }
            },
            __opts__=ResourceOptions(provider=custom_provider)
            )

# Create nginx service
service = Service(name,
    metadata={
        "labels": appLabels
    },
    spec={
        "ports": [
            {
                "name": "http",
                "port": 80
            }
        ],
        "selector": appLabels,
        "type": "LoadBalancer",
    },
    __opts__=ResourceOptions(provider=custom_provider)
)

# Export
pulumi.export('storage_connection_string', account.primary_connection_string)
pulumi.export('resource_group_id', rg.id)
pulumi.export('kubeconfig', aks.kube_config_raw)
pulumi.export('namespace_name', namespace.metadata.apply(lambda resource: resource['name']))
pulumi.export('deployment_name', deployment.metadata.apply(lambda resource: resource['name']))
pulumi.export('service_name', service.metadata.apply(lambda resource: resource['name']))
pulumi.export('service_public_endpoint', service.status.apply(lambda status: status['load_balancer']['ingress'][0]['ip']))
pulumi.export('sql_domain_name', sql.fully_qualified_domain_name)
