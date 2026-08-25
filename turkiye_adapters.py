# Turkey-specific adapter defaults and helpers

DEFAULTS = {
    "Robotistan": {
        "url_template": "https://www.robotistan.com/arama?q={sku}",
        "price_selector": ".product-price"  # placeholder; update if incorrect
    },
    "RobotHobi": {
        "url_template": "https://www.robothobi.com.tr/?s={sku}",
        "price_selector": ".price"  # placeholder; update if incorrect
    },
    "Hepsiburada": {
        "url_template": "https://www.hepsiburada.com/ara?q={sku}",
        "price_selector": ".price"  # placeholder; Hepsiburada often JS-rendered
    },
    "N11": {
        "url_template": "https://www.n11.com/arama?q={sku}",
        "price_selector": ".proDetailPrice"  # placeholder
    }
}

# Usage: import DEFAULTS and use DEFAULTS[site]["url_template"] / ["price_selector"]
