"""
Основной класс для мониторинга магазинов Etsy
"""
import time
import logging
from typing import List, Dict
from config.settings import config
from parsers.everbee_parser import EverBeeParser
from services.data_service import DataService
from services.tops_service import TopsService
from models.product import Product
from parsers.base_parser import ShopCheckError

class EtsyMonitor:
    """Основной класс для мониторинга магазинов Etsy"""
    
    def __init__(self):
        self.config = config
        self.parser = EverBeeParser(config)
        self.data_service = DataService(config)
        self.tops_service = TopsService(self.data_service.tops_dir)
    
    def parse_single_shop(self, shop_url: str, compare_with_previous: bool = True) -> str:
        """Парсит один магазин и сохраняет результат"""
        shop_name = self.parser.get_shop_name_from_url(shop_url)
        products = self.parser.parse_shop_page(shop_url)
        
        if not products:
            print(f"Не удалось получить данные для магазина {shop_name}")
            return None
        
        # Сохраняем данные
        filename = self.data_service.save_products_to_excel(products, shop_name)
        
        # Сравниваем с предыдущими данными если нужно
        if compare_with_previous:
            comparison = self.data_service.compare_shop_data(products, shop_name)
            if comparison:
                self.data_service.print_comparison_results(comparison)
                
                # Здесь в будущем будет отправка уведомлений в Telegram
                if comparison.has_changes:
                    print(f"🔔 Обнаружены изменения в магазине {shop_name}!")
        
        return filename
    
    def parse_all_shops(self, compare_with_previous: bool = True) -> Dict[str, List[Product]]:
        """Парсит все магазины"""
        urls = self.data_service.load_shop_urls()
        
        if not urls:
            print("Нет URL для парсинга")
            return {}
        
        all_shop_products = {}
        i = 0
        
        while i < len(urls):
            url = urls[i]
            shop_name = self.parser.get_shop_name_from_url(url)
            print(f"\n--- Парсинг магазина {i+1}/{len(urls)}: {shop_name} ({url}) ---")
            
            try:
                products = self.parser.parse_shop_page(url)
                
                if products:
                    # Сохраняем в Excel
                    excel_file = self.data_service.save_products_to_excel(products, shop_name)
                    
                    # Добавляем в общий словарь
                    all_shop_products[shop_name] = products
                    
                    print(f"✅ Магазин {shop_name} успешно обработан ({len(products)} товаров)")
                    
                    # Сравниваем с предыдущими данными если нужно
                    if compare_with_previous:
                        comparison = self.data_service.compare_shop_data(products, shop_name)
                        if comparison:
                            self.data_service.print_comparison_results(comparison)
                            
                            if comparison.has_changes:
                                print(f"🔔 Обнаружены изменения в магазине {shop_name}!")
                    
                    # Переходим к следующему магазину
                    i += 1
                    
                    # Пауза между запросами
                    if i < len(urls):
                        print(f"Пауза {self.config.etsy.request_delay} сек...")
                        time.sleep(self.config.etsy.request_delay)
                else:
                    print(f"✅ Магазин {shop_name}: новых товаров нет")
                    i += 1

            except ShopCheckError as e:
                print(f"❌ Магазин {shop_name} не удалось проверить: {e}")
                i += 1
            except Exception as e:
                print(f"❌ Критическая ошибка при парсинге {url}: {e}")
                print("🔄 Переходим к следующему магазину")
                i += 1
                continue
        
        return all_shop_products
    
    def run_monitoring_cycle(self):
        """Запускает один цикл мониторинга и возвращает результаты для бота"""
        from models.product import ShopComparison
        
        print("🚀 Запуск цикла мониторинга Etsy магазинов")
        print(f"Время: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Создаём папку для текущего сеанса парсинга
        parsing_dir = self.data_service.start_parsing_session()
        
        # Парсим все магазины
        all_shop_products = self.parse_all_shops(compare_with_previous=True)
        
        if not all_shop_products:
            print("❌ Не удалось получить данные ни от одного магазина")
            return []
        
        print(f"\n=== СОХРАНЕНИЕ РЕЗУЛЬТАТОВ ===")
        
        # Сначала сохраняем базовые результаты в JSON
        results_file = self.data_service.save_results_to_json(all_shop_products)
        
        # Сравниваем с предыдущими результатами и находим новые товары
        print("\n=== СРАВНЕНИЕ С ПРЕДЫДУЩИМИ РЕЗУЛЬТАТАМИ ===")
        
        # Формируем структуру для сравнения
        current_results = {}
        for shop_name, products in all_shop_products.items():
            current_results[shop_name] = {product.listing_id: product.url for product in products}
        
        # Находим новые товары
        new_products_dict = self.data_service.compare_all_shops_results(current_results)
        
        logging.debug(f"\n🔍 DEBUG: new_products_dict type = {type(new_products_dict)}")
        logging.debug(f"🔍 DEBUG: new_products_dict length = {len(new_products_dict) if new_products_dict else 0}")
        logging.debug(f"🔍 DEBUG: new_products_dict bool = {bool(new_products_dict)}")
        
        # Находим полные данные новых товаров
        new_products_full_data = {}
        for listing_id in new_products_dict:
            for shop_name, products in all_shop_products.items():
                for product in products:
                    if product.listing_id == listing_id:
                        new_products_full_data[listing_id] = product
                        break
        
        # Сохраняем финальные результаты с новыми товарами
        final_results_file = self.data_service.save_results_with_new_products(all_shop_products, new_products_dict, new_products_full_data)
        
        # Анализируем новые товары через EverBee
        logging.debug(f"\n🔍 DEBUG: Проверка условия для EverBee...")
        if new_products_dict:
            logging.info(f"✅ Условие выполнено! Запускаем EverBee анализ...")
            self.tops_service.process_new_products(new_products_dict, self.data_service.current_parsing_folder)
        else:
            logging.error(f"❌ Условие НЕ выполнено! new_products_dict пустой или None")
        
        # Формируем результаты для бота
        comparison_results = []
        
        for shop_name, products in all_shop_products.items():
            # Находим новые товары для этого магазина
            new_products_for_shop = []
            for product in products:
                if product.listing_id in new_products_dict:
                    new_products_for_shop.append(product)
            
            # Создаем объект сравнения
            comparison = ShopComparison(
                shop_name=shop_name,
                new_products=new_products_for_shop,
                removed_products=[],  # Пока не отслеживаем удаленные товары
                total_current=len(products),
                total_previous=len(products) - len(new_products_for_shop),
                comparison_date=None  # Будет установлена автоматически
            )
            
            comparison_results.append(comparison)
        
        print(f"\n=== ИТОГИ ЦИКЛА ===")
        print(f"Успешно обработано магазинов: {len(all_shop_products)}")
        print(f"Общее количество товаров: {sum(len(products) for products in all_shop_products.values())}")
        print(f"Найдено новых товаров: {len(new_products_dict)}")
        
        if all_shop_products:
            print("Обработанные магазины:")
            for shop_name, products in all_shop_products.items():
                print(f"  - {shop_name}: {len(products)} товаров")
        
        if new_products_dict:
            print(f"\nНовые товары ({len(new_products_dict)}):")
            for listing_id, url in list(new_products_dict.items())[:5]:  # Показываем первые 5
                print(f"  - {listing_id}: {url}")
            if len(new_products_dict) > 5:
                print(f"  ... и еще {len(new_products_dict) - 5} товаров")
        
        # Очищаем всю output папку, оставляя только текущую
        print(f"\n=== ОЧИСТКА OUTPUT ПАПКИ ===")
        print("Ожидание 2 секунды перед очисткой...")
        time.sleep(2)
        
        if self.data_service.cleanup_output_folder():
            print("✅ Output папка очищена, осталась только текущая папка")
        else:
            print("⚠️ Не удалось полностью очистить output папку")
        
        print("✅ Цикл мониторинга завершен")
        return comparison_results