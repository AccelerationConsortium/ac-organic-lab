app_name    = "ac-organic-lab-dashboard"
environment = "prod"

cpu           = 512
memory        = 1024
desired_count = 2
min_capacity  = 2
max_capacity  = 4

environment_variables = {
  LOG_LEVEL       = "warning"
  AUTH_BYPASS     = "false"
  ADMIN_EMAILS    = ""
  API_BACKEND_URL = "https://api.ac-organic-lab.your-domain.com"
}
secrets = {
  DATABASE_SECRET = "ac-db-master-password"
}

enable_versioning = true
price_class       = "PriceClass_100"
enable_cognito    = true
enable_waf        = true
rate_limit        = 10000
