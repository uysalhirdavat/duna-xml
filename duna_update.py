import requests
import re
import json
import html
import time
from bs4 import BeautifulSoup
from lxml import etree

BASE_URL = "https://www.duna.com.tr"

# İlk aşamada çalışmasını doğruladığımız kategori.
# Sonraki aşamada tüm Duna kategorilerini otomatik keşfedeceğiz.
CATEGORY_URLS = [
    "https://www.duna.com.tr/boya-tabancasi",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}

session = requests.Session()
session.headers.update(HEADERS)


def temizle(value):
    if value is None:
        return ""
    return str(value).strip()


def tl_fiyat_cevir(value):
    """
    1.127,22 -> 1127.22
    789,05   -> 789.05
    """
    if not value:
        return ""

    value = value.replace(".", "").replace(",", ".")
    value = re.sub(r"[^0-9.]", "", value)

    try:
        return f"{float(value):.2f}"
    except Exception:
        return ""


def sayfa_getir(url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def product_data_bul(html_text):
    """
    Duna/T-Soft sayfasındaki PRODUCT_DATA.push(JSON.parse(...))
    kayıtlarını yakalar.
    """
    products = []

    pattern = re.compile(
        r"PRODUCT_DATA\.push\(JSON\.parse\('((?:\\.|[^'])*)'\)\)",
        re.DOTALL
    )

    for match in pattern.finditer(html_text):
        raw = match.group(1)

        try:
            # JavaScript string içindeki escape karakterlerini çöz.
            decoded = bytes(raw, "utf-8").decode("unicode_escape")
            decoded = decoded.encode("latin1").decode("utf-8")
        except Exception:
            decoded = raw.replace("\\'", "'").replace('\\"', '"')

        try:
            data = json.loads(decoded)
            products.append(data)
        except Exception:
            continue

    return products


def kart_fiyati_bul(html_text, product_id):
    """
    PRODUCT_DATA id'si ile ürün kartındaki TL + KDV fiyatını eşleştirir.
    """
    soup = BeautifulSoup(html_text, "html.parser")

    card = soup.find(attrs={"data-id": str(product_id)})

    if not card:
        return ""

    text = card.get_text(" ", strip=True)

    # Öncelikle indirimli/satış fiyatına yakın TL değerlerini bul.
    matches = re.findall(
        r"([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})|[0-9]+,[0-9]{2})\s*TL",
        text,
        re.I
    )

    if not matches:
        return ""

    # Kartlarda liste fiyatı + satış fiyatı bulunabiliyor.
    # Son görünen fiyat genellikle aktif satış fiyatıdır.
    return tl_fiyat_cevir(matches[-1])


def detay_bilgileri(url):
    """
    Ürün detay sayfasından açıklama ve ilave görselleri toplar.
    """
    try:
        html_text = sayfa_getir(url)
    except Exception as exc:
        print("Detay sayfası alınamadı:", url, exc)
        return "", []

    soup = BeautifulSoup(html_text, "html.parser")

    description = ""

    # Duna/T-Soft ürün açıklamasının bulunabileceği alanları sırayla dene.
    selectors = [
        "#product-features",
        ".product-detail-description",
        ".product-description",
        ".productDetailDescription",
        "[itemprop='description']",
    ]

    for selector in selectors:
        node = soup.select_one(selector)

        if node:
            text = node.get_text(" ", strip=True)

            if len(text) > 30:
                description = str(node)
                break

    images = []

    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("data-original") or img.get("src")

        if not src:
            continue

        src = html.unescape(src)

        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = BASE_URL + src

        if (
            "duna.com.tr" in src
            and ("-O." in src or "-B." in src)
            and src not in images
        ):
            images.append(src)

    return description, images[:5]


def xml_eleman(parent, tag, value=""):
    node = etree.SubElement(parent, tag)

    if value is not None:
        node.text = temizle(value)

    return node


def urun_xml_ekle(root, product, category_html):
    item = etree.SubElement(root, "item")

    product_id = temizle(product.get("id"))
    code = temizle(
        product.get("code")
        or product.get("supplier_code")
    )

    name = temizle(product.get("name"))
    barcode = temizle(product.get("barcode"))
    brand = temizle(product.get("brand"))
    quantity = temizle(product.get("quantity", "0"))
    vat = temizle(product.get("vat", "20"))

    category = temizle(product.get("category"))
    category_path = temizle(product.get("category_path"))

    relative_url = temizle(product.get("url"))

    if relative_url.startswith("http"):
        product_url = relative_url
    else:
        product_url = BASE_URL + relative_url

    price = kart_fiyati_bul(category_html, product_id)

    description, detail_images = detay_bilgileri(product_url)

    source_image = temizle(product.get("image"))

    images = []

    if source_image:
        images.append(source_image)

    for image in detail_images:
        if image not in images:
            images.append(image)

    # Ana kategori
    main_category = ""

    if category_path:
        parts = [
            p.strip()
            for p in category_path.split(">")
            if p.strip()
        ]

        # "Tüm Ürün Grupları"ndan sonraki ilk gerçek kategori
        if len(parts) >= 2:
            main_category = parts[1]
        elif parts:
            main_category = parts[0]

    xml_eleman(item, "id", product_id)
    xml_eleman(item, "code", code)
    xml_eleman(item, "label", name)
    xml_eleman(item, "stock", quantity)

    details = etree.SubElement(item, "details")

    if description:
        details.text = etree.CDATA(description)
    else:
        details.text = etree.CDATA(
            f"<h2>{html.escape(name)}</h2>"
        )

    # Duna sayfasındaki fiyat TL + KDV olduğundan KDV dahil işaretlemiyoruz.
    xml_eleman(item, "currency", "TL")
    xml_eleman(item, "price1", price)
    xml_eleman(item, "tax", vat)

    xml_eleman(item, "barcode", barcode)
    xml_eleman(item, "brand", brand)

    xml_eleman(item, "mainCategory", main_category)
    xml_eleman(item, "category", category)
    xml_eleman(item, "subCategory", "")

    for i in range(5):
        value = images[i] if i < len(images) else ""
        xml_eleman(item, f"picture{i + 1}", value)

    xml_eleman(item, "sourceUrl", product_url)

    print(
        f"Eklendi: {code} | {name} | "
        f"stok={quantity} | fiyat={price}"
    )


def main():
    root = etree.Element("root")

    seen = set()

    for category_url in CATEGORY_URLS:
        print("Kategori okunuyor:", category_url)

        try:
            category_html = sayfa_getir(category_url)
        except Exception as exc:
            print("Kategori alınamadı:", exc)
            continue

        products = product_data_bul(category_html)

        print("Bulunan ürün:", len(products))

        for product in products:
            product_id = temizle(product.get("id"))

            if not product_id or product_id in seen:
                continue

            seen.add(product_id)

            try:
                urun_xml_ekle(
                    root,
                    product,
                    category_html
                )
            except Exception as exc:
                print(
                    "Ürün işlenirken hata:",
                    product_id,
                    exc
                )

            time.sleep(0.3)

    tree = etree.ElementTree(root)

    tree.write(
        "duna.xml",
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8"
    )

    print("")
    print("duna.xml güncellendi.")
    print("Toplam ürün:", len(seen))


if __name__ == "__main__":
    main()
