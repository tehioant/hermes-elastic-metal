# Plan-only tests of the production DNS module with Scaleway mocked:
# no credentials, remote state access, or live infrastructure changes.
mock_provider "scaleway" {}

variables {
  root_domain = "antelab.eu"
}

run "disabled_until_pages_hostname_is_supplied" {
  command = plan

  module {
    source = "./modules/portfolio-dns"
  }

  assert {
    condition     = length(scaleway_domain_record.portfolio) == 0
    error_message = "No portfolio DNS record should be planned without the actual Pages hostname."
  }

  assert {
    condition     = output.portfolio_fqdn == ""
    error_message = "Do not advertise an enabled portfolio domain before its DNS is configured."
  }
}

run "production_pages_cname" {
  command = plan

  module {
    source = "./modules/portfolio-dns"
  }

  variables {
    portfolio_pages_hostname = "test-portfolio.pages.dev"
  }

  assert {
    condition = (
      length(scaleway_domain_record.portfolio) == 1 &&
      scaleway_domain_record.portfolio[0].dns_zone == "antelab.eu" &&
      scaleway_domain_record.portfolio[0].name == "portfolio" &&
      scaleway_domain_record.portfolio[0].type == "CNAME" &&
      scaleway_domain_record.portfolio[0].data == "test-portfolio.pages.dev." &&
      scaleway_domain_record.portfolio[0].ttl == 300
    )
    error_message = "The portfolio must be a Scaleway CNAME to the absolute production Pages hostname."
  }

  assert {
    condition     = output.portfolio_fqdn == "portfolio.antelab.eu"
    error_message = "The enabled portfolio hostname must use the existing root domain."
  }
}

run "accepts_absolute_target_once" {
  command = plan

  module {
    source = "./modules/portfolio-dns"
  }

  variables {
    portfolio_pages_hostname = "test-portfolio.pages.dev."
  }

  assert {
    condition     = scaleway_domain_record.portfolio[0].data == "test-portfolio.pages.dev."
    error_message = "An existing DNS root dot must be preserved, not duplicated."
  }
}

run "uses_existing_zone_setting" {
  command = plan

  module {
    source = "./modules/portfolio-dns"
  }

  variables {
    root_domain              = "example.eu"
    portfolio_pages_hostname = "test-portfolio.pages.dev"
  }

  assert {
    condition = (
      scaleway_domain_record.portfolio[0].dns_zone == "example.eu" &&
      output.portfolio_fqdn == "portfolio.example.eu"
    )
    error_message = "Reuse root_domain; do not register or migrate a separate zone."
  }
}

run "rejects_url" {
  command = plan

  module {
    source = "./modules/portfolio-dns"
  }
  variables {
    portfolio_pages_hostname = "https://test-portfolio.pages.dev"
  }
  expect_failures = [var.portfolio_pages_hostname]
}

run "rejects_path" {
  command = plan

  module {
    source = "./modules/portfolio-dns"
  }
  variables {
    portfolio_pages_hostname = "test-portfolio.pages.dev/path"
  }
  expect_failures = [var.portfolio_pages_hostname]
}

run "rejects_preview_hostname" {
  command = plan

  module {
    source = "./modules/portfolio-dns"
  }
  variables {
    portfolio_pages_hostname = "abc123.test-portfolio.pages.dev"
  }
  expect_failures = [var.portfolio_pages_hostname]
}

run "rejects_other_domain" {
  command = plan

  module {
    source = "./modules/portfolio-dns"
  }
  variables {
    portfolio_pages_hostname = "test-portfolio.example.com"
  }
  expect_failures = [var.portfolio_pages_hostname]
}

run "rejects_invalid_project_label" {
  command = plan

  module {
    source = "./modules/portfolio-dns"
  }
  variables {
    portfolio_pages_hostname = "-invalid.pages.dev"
  }
  expect_failures = [var.portfolio_pages_hostname]
}

run "rejects_whitespace" {
  command = plan

  module {
    source = "./modules/portfolio-dns"
  }
  variables {
    portfolio_pages_hostname = " test-portfolio.pages.dev "
  }
  expect_failures = [var.portfolio_pages_hostname]
}

run "rejects_oversized_project_label" {
  command = plan

  module {
    source = "./modules/portfolio-dns"
  }
  variables {
    portfolio_pages_hostname = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pages.dev"
  }
  expect_failures = [var.portfolio_pages_hostname]
}

run "accepts_single_character_project_label" {
  command = plan

  module {
    source = "./modules/portfolio-dns"
  }
  variables {
    portfolio_pages_hostname = "a.pages.dev"
  }
  assert {
    condition     = scaleway_domain_record.portfolio[0].data == "a.pages.dev."
    error_message = "A single-character DNS label is valid."
  }
}
