app_name    = "ac-organic-lab-dashboard"
environment = "dev"

cpu           = 256
memory        = 512
desired_count = 1
min_capacity  = 1
max_capacity  = 2

environment_variables = {
  LOG_LEVEL       = "debug"
  AUTH_BYPASS     = "true"
  API_BACKEND_URL = "https://api.ac-organic-lab-dev.your-domain.com"
}
secrets = {}

enable_versioning = false
price_class       = "PriceClass_100"
enable_cognito    = false
enable_waf        = false
rate_limit        = 10000
