locals {
  name_prefix = "ai-reliability-prod"

  common_tags = {
    Environment = "production"
    ManagedBy   = "terraform"
    Project     = "ai-reliability-platform"
  }
}

resource "azurerm_resource_group" "main" {
  name     = "${local.name_prefix}-rg"
  location = var.location
  tags     = local.common_tags
}

module "network" {
  source = "../../modules/network"

  name_prefix         = local.name_prefix
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  tags                = local.common_tags
}

module "aks" {
  source = "../../modules/aks"

  name_prefix         = local.name_prefix
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  subnet_id           = module.network.aks_subnet_id
}

module "acr" {
  source = "../../modules/acr"

  name_prefix         = local.name_prefix
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
}

module "database" {
  source = "../../modules/database"

  name_prefix         = local.name_prefix
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
}

module "keyvault" {
  source = "../../modules/keyvault"

  name_prefix         = local.name_prefix
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
}
