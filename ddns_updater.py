﻿"""
Dynamic DNS Updater for domain.tenten.vn
Automates A record updates using Playwright browser automation
"""
import asyncio
import json
import logging
import random
import sys
from typing import Optional, Dict, Any, List
import argparse
from playwright.async_api import async_playwright, Page, BrowserContext, ViewportSize
import requests

class TentenDDNSUpdater:
    # Class constants for selectors
    USERNAME_SELECTORS = [
        'input[name="username"]',
        'input[type="email"]',
        '#username',
        'input[placeholder*="username" i]',
        'input[placeholder*="email" i]'
    ]

    PASSWORD_SELECTORS = [
        'input[name="password"]',
        'input[type="password"]',
        '#password'
    ]

    SUBMIT_SELECTORS = [
        'button[type="submit"]',
        'input[type="submit"]',
        'input[name="submit"]',
        'button:has-text("Login")',
        'button:has-text("Đăng nhập")',
        '.btn-login'
    ]

    ERROR_SELECTORS = [
        '.error', '.alert-danger', '.login-error',
        '[class*="error"]', '[class*="alert"]'
    ]

    DNS_SETTINGS_URL = "https://domain.tenten.vn/ApiDnsSetting"
    LOGIN_TIMEOUT = 15000
    RECAPTCHA_TIMEOUT = 2000
    NETWORK_IDLE_TIMEOUT = 10000

    def __init__(self, config_path: str = "config.json"):
        self.config = self.load_config(config_path)
        self.logger = self.setup_logging()
        self.page: Optional[Page] = None
        self.browser : Optional[BrowserContext] = None

    def load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Config file not found. Created sample config at {config_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in config file: {e}")

    def setup_logging(self) -> logging.Logger:
        """Setup logging configuration"""
        log_config = self.config.get("logging", {})
        log_level = getattr(logging, log_config.get("level", "INFO"))
        log_file = log_config.get("file", "ddns_updater.log")

        logger = logging.getLogger("TentenDDNS")
        logger.setLevel(log_level)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)

        # File handler
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(log_level)

        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        console_handler.setFormatter(formatter)
        file_handler.setFormatter(formatter)

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

        return logger

    async def get_current_ip(self) -> str:
        """Get current public IP address"""
        try:
            # Using multiple IP services for reliability
            services = [
                "https://api.ipify.org",
                "https://ifconfig.me/ip",
                "https://icanhazip.com"
            ]

            for service in services:
                try:
                    response = requests.get(service, timeout=10)
                    if response.status_code == 200:
                        ip = response.text.strip()
                        self.logger.info(f"Current public IP: {ip}")
                        return ip
                except requests.RequestException:
                    continue

            raise Exception("Could not determine public IP address")

        except Exception as e:
            self.logger.error(f"Error getting current IP: {e}")
            raise

    async def init_browser(self):
        """Initialize Playwright browser"""
        try:
            self.playwright = await async_playwright().start()
            browser_settings = self.config.get("browser_settings", {})

            self.browser = await self.playwright.chromium.launch_persistent_context(
                headless=browser_settings.get("headless", False),
                args=browser_settings.get("args"),
                ignore_default_args= browser_settings.get("ignore_default_args"),
                user_agent=browser_settings.get("user_agent", ""),
                user_data_dir=browser_settings.get("user_data_dir", ""),
                viewport= ViewportSize(browser_settings.get("viewport", {})) if browser_settings.get("viewport") else None,
                device_scale_factor=browser_settings.get("device_scale_factor", 1),
                locale= browser_settings.get("locale", "vi-VN"),
                timezone_id=browser_settings.get("timezone_id", "Asia/Ho_Chi_Minh")
            )

            self.page = self.browser.pages[0] if self.browser.pages else await self.browser.new_page()
            self.page.set_default_timeout(browser_settings.get("timeout", 30000))

            self.logger.info("Browser initialized successfully")

        except Exception as e:
            self.logger.error(f"Error initializing browser: {e}")
            raise

    async def find_element(self, selectors: List[str], context: Optional[Any] = None) -> Optional[Any]:
        """Helper method to find an element using multiple selectors"""
        search_context = context or self.page
        for selector in selectors:
            try:
                element = await search_context.query_selector(selector)
                if element:
                    await element.focus() if await element.evaluate('el => el.tagName.toLowerCase() in ["input", "textarea"]') else None
                    return element
            except:
                continue
        return None

    async def check_login_status(self) -> bool:
        """Check if login was successful and handle errors"""
        current_url = self.page.url
        if "login" not in current_url.lower():
            return True

        error_element = await self.find_element(self.ERROR_SELECTORS)
        if error_element:
            error_text = await error_element.inner_text()
            self.logger.error(f"Login error: {error_text}")
            return False

        self.logger.error("Login failed - still on login page")
        return False

    async def wait_for_recaptcha_completion(self, timeout: int = 30) -> bool:
        """Wait for reCAPTCHA to complete with periodic checks"""
        start_time = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_time < timeout:
            # Check if reCAPTCHA is completed
            completed = await self.page.evaluate('''
                () => {
                    // Check for reCAPTCHA v3 completion indicators
                    const recaptchaElements = document.querySelectorAll('[data-sitekey]');
                    const iframes = document.querySelectorAll('iframe[src*="recaptcha"]');
                    
                    // Check if grecaptcha object exists and has a response
                    if (typeof grecaptcha !== 'undefined') {
                        try {
                            const response = grecaptcha.getResponse();
                            if (response && response.length > 0) {
                                return true;
                            }
                        } catch (e) {
                            // Ignore errors
                        }
                    }
                    
                    // Check for completed visual indicators
                    const completedElements = document.querySelectorAll('.recaptcha-checkbox-checked, .recaptcha-success');
                    if (completedElements.length > 0) {
                        return true;
                    }
                    
                    // Check for hidden reCAPTCHA completion
                    const hiddenInputs = document.querySelectorAll('input[name="recaptchaToken"]');
                    for (const input of hiddenInputs) {
                        if (input.value && input.value.length > 0) {
                            return true;
                        }
                    }
                    
                    return false;
                }
            ''')

            if completed:
                return True

            # TODO: Continue human-like behavior while waiting
            await asyncio.sleep(random.uniform(0.5, 1.0))

        return False

    async def login(self) -> bool:
        """Login to tenten.vn domain management"""
        try:
            await self.page.wait_for_load_state('networkidle', timeout=self.NETWORK_IDLE_TIMEOUT)
            credentials = self.config["credentials"]
            username = credentials["username"]
            password = credentials["password"]

            self.logger.info("Navigating to DNS settings page...")
            await self.page.goto(self.DNS_SETTINGS_URL)

            # Check if already logged in
            if "login" not in self.page.url.lower():
                self.logger.info("Already logged in, proceeding to DNS settings")
                return True

            self.logger.info("Redirected to login page, attempting login...")
            await self.page.wait_for_selector('input[name="username"], input[type="email"], #username',
                                            timeout=self.LOGIN_TIMEOUT)

            # Find and fill username field
            username_field = await self.find_element(self.USERNAME_SELECTORS)
            if not username_field:
                raise Exception("Could not find username/email field")
            await username_field.fill(username)

            await self.page.wait_for_timeout(1000)  # Wait for a second to

            # Find and fill password field
            password_field = await self.find_element(self.PASSWORD_SELECTORS)
            if not password_field:
                raise Exception("Could not find password field")
            await password_field.fill(password)

            await self.page.wait_for_timeout(1000)

            # Submit form
            submit_btn = await self.find_element(self.SUBMIT_SELECTORS)
            if not submit_btn:
                raise Exception("Could not find submit button")

            await self.page.wait_for_load_state('networkidle', timeout=self.NETWORK_IDLE_TIMEOUT)
            await self.page.wait_for_timeout(5000)  # Wait for any potential reCAPTCHA to load
            await self.wait_for_recaptcha_completion()
            await submit_btn.click()

            # Wait for navigation and check login status
            await self.page.wait_for_load_state("networkidle", timeout=self.LOGIN_TIMEOUT)
            if not await self.check_login_status():
                return False

            self.logger.info("Login successful")
            await self.page.goto(self.DNS_SETTINGS_URL)
            await self.page.wait_for_load_state("networkidle", timeout=self.NETWORK_IDLE_TIMEOUT)
            return True

        except Exception as e:
            self.logger.error(f"Login failed: {e}")
            return False

    async def update_dns_record(self, new_ip: str) -> bool:
        """Update DNS A record with new IP"""
        try:
            await self.page.wait_for_load_state("networkidle", timeout=self.NETWORK_IDLE_TIMEOUT)
            domain_settings = self.config["domain_settings"]
            configuration_by_ip_btn_text = domain_settings["configuration_by_ip_btn_text"]

            self.logger.info(f"Updating DNS record to {new_ip}")
            await self.page.wait_for_load_state("networkidle", timeout=self.NETWORK_IDLE_TIMEOUT)

            # Find domain row or DNS management interface
            domain_selectors = [
                f'tr:has-text("{new_ip}")',
                f'td:has-text("{new_ip}")'
            ]
            is_exiting_domain = False
            for selector in domain_selectors:
                try:
                    record = await self.page.wait_for_selector(selector, timeout=1000)
                    if record:
                        self.logger.info(f"Found domain row for {new_ip}")
                        is_exiting_domain = True
                        break
                except:
                    continue
            if not is_exiting_domain:
                self.logger.warning(f"{new_ip} not found, configuration by IP")
                configuration_by_ip_selectors = [
                    f'tr:has-text("{configuration_by_ip_btn_text}")',
                    'li.ip_popup > a',
                    f'li:has-text("{configuration_by_ip_btn_text}") a',
                ]
                configuration_by_ip_btn = await self.find_element(configuration_by_ip_selectors)
                if not configuration_by_ip_btn:
                    self.logger.error(f"Could not find configuration by IP button for {new_ip}")
                    return False

                self.logger.info(f"Found configuration by IP button: {configuration_by_ip_btn_text}")
                await configuration_by_ip_btn.click()
                await self.page.wait_for_timeout(1000)
                ip_input = await self.find_element(['#ip', 'input[type="text"]'])
                if not ip_input:
                    self.logger.error("Could not find IP input field in configuration by IP form")
                    return False
                await ip_input.fill(new_ip)
                submit_btn = await self.find_element(['#send', 'button[type="submit"]'])
                if not submit_btn:
                    self.logger.error("Could not find submit button in configuration by IP form")
                    return False
                await submit_btn.click()
                self.logger.info(f"Configuration by IP submitted for {new_ip}")
                await self.page.wait_for_load_state("networkidle", timeout=self.NETWORK_IDLE_TIMEOUT)
                await self.page.wait_for_timeout(5000)
                self.logger.info(f"Updated config with new IP: {new_ip}")
                return True
            else:
                self.logger.info(f"Found domain row for {new_ip}, dont need configuration by IP")
                return True

        except Exception as e:
            self.logger.error(f"Error updating DNS record: {e}")
            return False

    async def cleanup(self):
        """Clean up browser resources"""
        try:
            await self.page.close() if self.page else None
            await self.browser.close() if self.browser else None
            await self.playwright.stop() if hasattr(self, 'playwright') else None
            self.logger.info("Browser cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

    async def run(self, target_ip: Optional[str] = None) -> bool:
        """Main execution method"""
        try:
            # Get current IP if not provided
            if target_ip is None:
                target_ip = await self.get_current_ip()

            # Initialize browser
            await self.init_browser()

            # Login and update DNS
            success = await self.login()
            attempts = 3
            retries = 0
            while not success and retries < attempts:
                retries += 1
                success = await self.login()

            if success:
                success = await self.update_dns_record(target_ip)
                if success:
                    self.logger.info(f"DNS update successful! New IP: {target_ip}")
                    return True
                else:
                    self.logger.error("DNS update failed")
                    return False
            else:
                self.logger.error("Login failed")
                return False

        except Exception as e:
            self.logger.error(f"Execution failed: {e}")
            return False
        finally:
            await self.cleanup()


async def main():
    parser = argparse.ArgumentParser(description="Dynamic DNS Updater for tenten.vn")
    parser.add_argument("--config", "-c", default="config.json",
                        help="Path to configuration file")
    parser.add_argument("--ip", "-i", help="Target IP address (auto-detect if not provided)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose logging")

    args = parser.parse_args()

    try:
        updater = TentenDDNSUpdater(args.config)

        if args.verbose:
            updater.logger.setLevel(logging.DEBUG)

        success = await updater.run(args.ip)
        sys.exit(0 if success else 1)

    except FileNotFoundError as e:
        print(f"Configuration error: {e}")
        print("Please edit the config.json file with your credentials and settings.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())