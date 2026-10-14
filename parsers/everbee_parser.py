"""
Парсер для магазинов Etsy через EverBee API
"""
import logging
from typing import List, Optional
from parsers.base_parser import BaseParser, ShopCheckError
from models.product import Product
from utils.everbee_client import EverBeeClient


class EverBeeParser(BaseParser):
    """Парсер для магазинов Etsy через EverBee API"""
    
    def __init__(self, config):
        super().__init__(config)
        self.everbee_client = EverBeeClient()
    
    def get_shop_name_from_url(self, url: str) -> str:
        """Извлекает название магазина из URL"""
        import re
        try:
            match = re.search(r'/shop/([^/?]+)', url)
            if match:
                return match.group(1)
            return "unknown_shop"
        except:
            return "unknown_shop"
    
    def parse_shop_page(self, shop_url: str) -> List[Product]:
        """Парсит магазин через EverBee API с сортировкой по новизне.

        Возвращает [] если новых товаров нет.
        Бросает ShopCheckError если магазин проверить не удалось — иначе в
        логе невозможно отличить «новых нет» от «магазин не открылся».
        """
        shop_name = self.get_shop_name_from_url(shop_url)

        logging.info(f"📄 Парсим магазин через EverBee API: {shop_name}")

        # Проверяем токен перед запросом
        if not self.everbee_client.ensure_token():
            logging.error(f"❌ Не удалось получить валидный токен для магазина {shop_name}")
            raise ShopCheckError("токен EverBee не получен (проверьте логин/пароль)")

        # Получаем листинги отсортированные по возрасту (новые сначала)
        response = self.everbee_client.get_shop_listings(
            shop_name=shop_name,
            order_by="listing_age_in_months",
            time_range="last_1_month",
            order_direction="asc",
            page=1,
            per_page=50
        )

        if not response:
            reason = getattr(self.everbee_client, "last_error", None) or "EverBee не вернул данные"
            logging.error(f"❌ Не удалось получить данные для магазина {shop_name}: {reason}")
            raise ShopCheckError(reason)

        # Извлекаем листинги из ответа
        listings = response.get('results', [])

        if not listings:
            # Магазин проверили, просто за последний месяц новинок нет.
            logging.info(f"✅ У магазина {shop_name} нет новых товаров за месяц")
            return []
        
        products = []
        for listing in listings:
            try:
                # Фильтруем по возрасту листинга (максимум 2 месяца)
                listing_age = listing.get('listing_age_in_months', 0)
                if listing_age >= 2:
                    continue
                    
                product = self._parse_listing_data(listing, shop_name)
                if product:
                    products.append(product)
            except Exception as e:
                logging.error(f"❌ Ошибка при парсинге листинга {listing.get('listing_id', 'unknown')}: {e}")
                continue
        
        logging.info(f"✅ Найдено товаров: {len(products)}")
        return products
    
    def _parse_listing_data(self, listing: dict, shop_name: str) -> Optional[Product]:
        """Преобразует данные листинга из EverBee в объект Product"""
        listing_id = str(listing.get('listing_id', ''))
        title = listing.get('title', 'Без названия')
        url = listing.get('url', '')
        price = listing.get('price')
        currency = listing.get('currency_code', 'USD')
        
        # Получаем изображение
        image_url = None
        if 'Images' in listing:
            image_url = listing['Images']
        
        return Product(
            listing_id=listing_id,
            title=title,
            url=url,
            shop_name=shop_name,
            price=price,
            currency=currency,
            image_url=image_url
        )
    
    def close_browser(self):
        """Заглушка для совместимости с интерфейсом"""
        pass