from flask import Flask, jsonify, abort

app = Flask(__name__)

# Ваши данные (вместо базы данных)
DATA = {
    "Example": {
        "NmNhWaZv2hJVG0r2n8U432fXEg7XeIsyA0Q1zsL0kh826sF08wR9xdGoxyWs2aMNLk9fGAD4RkhV9DFESuEBOshSsm": "DiasDamina",
        "jPwIhjye7bp6fMA57mnucL5sMIC7BElWZhxlHfui0CqtLk5nnr": "OhNoBabyCryy"
    },
    "Product1": {
        "WQDqqx2j3Fh9oCG7EmFCIhimgxW3AobF1IZpVXsZH4uVYjK8gM": "alia_sarik",
        "zcfAmGDJ3kFT4FKh5so6UUbkL31U6W9yywlUw60U0UVB1VeyBi": "Robzilain4",
        "Hcrer8gzRiJQm3P8Pnhfo0kw7m6tCjzfcDYQChuPB5NwvaX25O": "Damil_100",
        "DJZZBnEa1nLn7TxoRddu8vQA8Qyae6MAkI94ARI7dQyJn5Iq4S": "DiasDamina",
        "LOXqLVRfXr1TwvlDnETSPMGPnnuRoHKZmXxE0gQW9sV04YN5xw": "OhNoBabyCryy"
    },
    "McDonald": {
        "rnfJsA_Z-.2DFT%hbtO3W=F897xITO4WFymgaE1s": "alia_sarik",
        "mUs6vW4MD994vNUQW7whMKb7u7hUwPGj": "DiasDamina"
    },
    "BEPC Electricity": {
        "FWR2E9n5iRqUzTlCfRuz6gy4u6jOnKlm": "DiasDamina",
        "wx546G0rUZa8qwDSsTVz4DCRY8MfzboZ": "OhNoBabyCryy"
    },
    "AntiCheat": {
        "3nJdiZDaEublqS4VjbhUDXxJQ7AYq5glvmfTorrPFXsVYens55": "DiasDamina",
        "5wjbwdZlcdjt8zfoS73LFmn2lo38XtqqRLvFbvUOFZNlVQES8d": "OhNoBabyCryy"
    },
    "k9AfI9TFE202Zw6h2lgxpHHvk": {
        "b02MjsgbNjLp2emTz1dhRC3xHtb1j7NxF4c": "diasdamina",
        "admin": "DiasDamina",
        "admin2": "OhNoBabyCryy"
    },
    "AI Bot": {
        "4092db0f-0dad-4c2e-9da3-5b12408eb0f2": "OhNoBabyCryy"
    },
    "Proxy Service": {
        "07e62944-7023-4c7b-aebf-d788a9c9b5b5": "DiasDamina"
    }
}


@app.route('/users/v1/<username>/<product_name>')
def check_ownership(username, product_name):
    """
    Проверяет, есть ли у пользователя доступ к продукту.

    Args:
        username (str): Имя пользователя.
        product_name (str): Название продукта (с пробелами, как в исходных данных).

    Returns:
        JSON: Объект с информацией о пользователе и владении продуктом.
    """

    product_name = product_name.replace("%20", " ") #декодируем URL если есть пробелы

    if product_name not in DATA:
        abort(404, description=f"Product '{product_name}' not found")

    product_data = DATA[product_name]

    is_owner = False
    for key, owner in product_data.items():
        if owner == username:
            is_owner = True
            break

    return jsonify({
        "username": username,
        "isOwner": is_owner,
        "product": product_name
    })



@app.errorhandler(404)
def resource_not_found(e):
    """Обработчик ошибки 404 (Not Found)."""
    return jsonify(error=str(e)), 404


if __name__ == '__main__':
    app.run(debug=True)  # debug=True для разработки, уберите в продакшене!
