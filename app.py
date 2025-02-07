# app.py
from flask import Flask, jsonify, abort, request
from data import DATA
import urllib.parse

app = Flask(__name__)

@app.route('/users/v1/<username>/<product_name>', methods=['GET'])
def get_user_product(username, product_name):
    """
    Обрабатывает GET-запросы к API.

    Args:
        username (str): Имя пользователя (ник).
        product_name (str): Название продукта (с пробелами, закодированными как %20).

    Returns:
        JSON:  Ответ в формате JSON, содержащий информацию о пользователе и продукте.
               Если пользователь владеет продуктом, isOwner будет True, иначе False.
               В случае ошибки возвращается 404 Not Found.
    """
    decoded_product_name = urllib.parse.unquote(product_name) # Декодируем URL
    
    # Проверяем наличие продукта в данных.  Используем .get() для безопасного доступа.
    product_data = DATA.get(decoded_product_name)
    if product_data is None:
        abort(404, description=f"Product '{decoded_product_name}' not found")

    # Ищем пользователя в данных продукта.
    found = False
    for key, user in product_data.items():
        if user.lower() == username.lower():
            found = True
            break  # Выходим из цикла, как только нашли пользователя

    # Формируем ответ.
    response_data = {
        "username": username,
        "isOwner": found,
        "product": decoded_product_name
    }

    return jsonify(response_data)

@app.errorhandler(404)
def resource_not_found(e):
    """
    Обработчик ошибок 404.  Возвращает JSON-ответ с описанием ошибки.
    """
    return jsonify(error=str(e)), 404

if __name__ == '__main__':
    # Запуск приложения (только для локальной разработки, не для Heroku)
    # app.run(debug=True)  # debug=True  -  ОТКЛЮЧИТЬ в продакшене!
    pass # Убираем запуск через python app.py, heroku сам запустит через gunicorn
