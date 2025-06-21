import json


def parse_offer_json(raw: str) -> list[dict]:
    data = json.loads(raw)

    result: list[dict] = []
    for title, variants in data.items():
        for v in variants:
            delivery = v.get("delivery")
            delivery = None if not delivery or str(delivery).lower() == "в наличии" else delivery

            result.append({
                "title": title,
                "brand": v.get("brand"),
                "price": v.get("price"),
                "stock": v.get("stock"),
                "delivery": delivery,
            })
    return result
