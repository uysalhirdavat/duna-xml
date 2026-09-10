import requests
import re
import json
import html
import time
import os
from collections import Counter
from urllib.parse import quote, urljoin, urlparse
from bs4 import BeautifulSoup
from lxml import etree

BASE_URL = "https://www.duna.com.tr"
CATALOG_URL = BASE_URL + "/all-products"

# Kategori keşfi başarısız olursa test kategorisi yine çalışmaya devam eder.
FALLBACK_CATEGORY_URLS = [
    BASE_URL + "/boya-tabancasi",
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
    if value is None:
        return ""
    value = str(value).strip()
    value = re.sub(r"[^0-9,.\-]", "", value)
    if not value:
        return ""

    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        value = value.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", value):
        value = value.replace(".", "")

    try:
        price = float(value)
        if price <= 0:
            return ""
        return f"{price:.2f}"
    except Exception:
        return ""


def duna_giris_yap():
    username = os.environ.get("DUNA_USERNAME", "").strip()
    password = os.environ.get("DUNA_PASSWORD", "")

    if not username or not password:
        raise RuntimeError("DUNA_USERNAME veya DUNA_PASSWORD GitHub Secret bulunamadi.")

    giris_sayfasi = BASE_URL + "/bayi-girisi-sayfasi"
    start = session.get(giris_sayfasi, timeout=30, allow_redirects=True)
    start.raise_for_status()

    login_url = (
        BASE_URL
        + "/srv/customer/signin/email/"
        + quote(username, safe="")
        + "?language=tr"
    )

    login_headers = {
        "Referer": giris_sayfasi,
        "Origin": BASE_URL,
        "X-Requested-With": "XMLHttpRequest",
    }

    response = session.post(
        login_url,
        data={"password": password, "remember": "1"},
        headers=login_headers,
        timeout=30,
        allow_redirects=True,
    )
    response.raise_for_status()

    check = session.get(BASE_URL + "/uye-siparisleri", timeout=30, allow_redirects=True)
    check.raise_for_status()

    if "uye-siparisleri" not in check.url.lower():
        raise RuntimeError(
            "Duna bayi girisi basarisiz. Kullanici adi/sifre veya giris akisi kontrol edilmeli."
        )

    print("Duna bayi girisi basarili.")


def sayfa_getir(url):
    response = session.get(url, timeout=30, allow_redirects=True)
    response.raise_for_status()
    return response.text


def product_data_bul(html_text):
    products = []
    pattern = re.compile(
        r"PRODUCT_DATA\.push\(JSON\.parse\('((?:\\.|[^'])*)'\)\)",
        re.DOTALL
    )
    for match in pattern.finditer(html_text):
        raw = match.group(1)
        try:
            decoded = bytes(raw, "utf-8").decode("unicode_escape")
            decoded = decoded.encode("latin1").decode("utf-8")
        except Exception:
            decoded = raw.replace("\\'", "'").replace('\\"', '"')
        try:
            products.append(json.loads(decoded))
        except Exception:
            continue
    return products


def kategori_adayi_mi(url):
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc not in {"www.duna.com.tr", "duna.com.tr"}:
        return False

    path = parsed.path.rstrip("/")
    if not path or path == "":
        return False

    yasakli = (
        "/srv/", "/resources/", "/uploads/", "/uye-", "/bayi-",
        "/sepet", "/favori", "/iletisim", "/hakkimizda", "/blog",
        "/marka", "/search", "/arama", "/siparis", "/odeme",
        "/customer", "/account", "/login", "/signin",
    )
    if any(x in path.lower() for x in yasakli):
        return False

    uzantilar = (
        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg",
        ".pdf", ".css", ".js", ".xml", ".ico",
    )
    if path.lower().endswith(uzantilar):
        return False

    # Duna ürün sayfaları çoğunlukla sonda sayısal ürün kimliği taşır.
    # Bunları kategori adayı olarak taramayarak binlerce gereksiz isteği önlüyoruz.
    if re.search(r"-\d+$", path):
        return False

    return True


def kategori_url_kesfet():
    print("Kategori listesi kesfediliyor:", CATALOG_URL)

    try:
        katalog_html = sayfa_getir(CATALOG_URL)
    except Exception as exc:
        print("Kategori kesfi basarisiz, fallback kullanilacak:", exc)
        return FALLBACK_CATEGORY_URLS[:]

    soup = BeautifulSoup(katalog_html, "html.parser")
    href_sayilari = Counter()

    for a in soup.find_all("a", href=True):
        href = html.unescape(a.get("href", "")).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        absolute = urljoin(BASE_URL + "/", href)
        parsed = urlparse(absolute)
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

        if kategori_adayi_mi(clean_url):
            href_sayilari[clean_url] += 1

    # Menü ve kategori ağacındaki bağlantılar masaüstü/mobil alanlarda tekrar eder.
    # Tek geçen linklerin çoğu ürün/kampanya gibi sayfalardır.
    adaylar = [url for url, count in href_sayilari.items() if count >= 2]

    # Katalog sayfasının kendisini ve çalışan test kategorisini her zaman dahil et.
    adaylar.insert(0, CATALOG_URL)
    for url in FALLBACK_CATEGORY_URLS:
        if url not in adaylar:
            adaylar.append(url)

    print("Ham kategori adayi:", len(adaylar))

    kategoriler = []
    for i, url in enumerate(adaylar, 1):
        try:
            page_html = katalog_html if url == CATALOG_URL else sayfa_getir(url)
            products = product_data_bul(page_html)
            if products:
                kategoriler.append(url)
                print(f"Kategori bulundu ({i}/{len(adaylar)}): {url} | urun={len(products)}")
        except Exception as exc:
            print("Kategori adayi okunamadi:", url, exc)

        time.sleep(0.05)

    # Aynı URL'leri korumalı biçimde tekilleştir.
    kategoriler = list(dict.fromkeys(kategoriler))

    if not kategoriler:
        print("Hic kategori dogrulanamadi, fallback kullaniliyor.")
        return FALLBACK_CATEGORY_URLS[:]

    print("Dogrulanan kategori sayisi:", len(kategoriler))
    return kategoriler


def html_icinden_fiyat_bul(node):
    if not node:
        return ""

    possible_attrs = [
        "data-price", "data-sale-price", "data-product-price",
        "data-discount-price", "data-final-price", "data-price1",
        "data-price-without-vat",
    ]

    for tag in node.find_all(True):
        for attr in possible_attrs:
            value = tag.get(attr)
            if value:
                fiyat = tl_fiyat_cevir(value)
                if fiyat:
                    return fiyat

    price_tag = node.find(attrs={"itemprop": "price"})
    if price_tag:
        value = price_tag.get("content") or price_tag.get("value") or price_tag.get_text(" ", strip=True)
        fiyat = tl_fiyat_cevir(value)
        if fiyat:
            return fiyat

    raw_html = str(node)
    patterns = [
        r'"salePrice"\s*:\s*"?([0-9.,]+)',
        r'"discountedPrice"\s*:\s*"?([0-9.,]+)',
        r'"finalPrice"\s*:\s*"?([0-9.,]+)',
        r'"price1"\s*:\s*"?([0-9.,]+)',
        r'"price"\s*:\s*"?([0-9.,]+)',
        r'data-price=["\']([0-9.,]+)',
        r'data-sale-price=["\']([0-9.,]+)',
        r'data-product-price=["\']([0-9.,]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_html, re.I)
        if match:
            fiyat = tl_fiyat_cevir(match.group(1))
            if fiyat:
                return fiyat

    text = node.get_text(" ", strip=True)
    matches = re.findall(
        r"([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})|[0-9]+,[0-9]{2})\s*(?:TL|₺)",
        text,
        re.I,
    )
    if matches:
        fiyat = tl_fiyat_cevir(matches[-1])
        if fiyat:
            return fiyat
    return ""


def kart_fiyati_bul(html_text, product_id, product_url=""):
    soup = BeautifulSoup(html_text, "html.parser")
    card = soup.find(attrs={"data-id": str(product_id)})
    if card:
        fiyat = html_icinden_fiyat_bul(card)
        if fiyat:
            return fiyat
        parent = card
        for _ in range(6):
            parent = parent.parent
            if not parent:
                break
            fiyat = html_icinden_fiyat_bul(parent)
            if fiyat:
                return fiyat

    if product_url:
        try:
            detail_soup = BeautifulSoup(sayfa_getir(product_url), "html.parser")
            fiyat = html_icinden_fiyat_bul(detail_soup)
            if fiyat:
                return fiyat
        except Exception as exc:
            print("Fiyat icin detay sayfasi okunamadi:", product_url, exc)
    return ""


def detay_bilgileri(url):
    try:
        html_text = sayfa_getir(url)
    except Exception as exc:
        print("Detay sayfasi alinamadi:", url, exc)
        return "", []

    soup = BeautifulSoup(html_text, "html.parser")
    description = ""
    selectors = [
        "#product-features", ".product-detail-description", ".product-description",
        ".productDetailDescription", "[itemprop='description']",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node and len(node.get_text(" ", strip=True)) > 30:
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
        if "duna.com.tr" in src and ("-O." in src or "-B." in src) and src not in images:
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
    code = temizle(product.get("code") or product.get("supplier_code"))
    name = temizle(product.get("name"))
    barcode = temizle(product.get("barcode"))
    brand = temizle(product.get("brand"))
    quantity = temizle(product.get("quantity", "0"))
    vat = temizle(product.get("vat", "20"))
    category = temizle(product.get("category"))
    category_path = temizle(product.get("category_path"))
    relative_url = temizle(product.get("url"))
    product_url = relative_url if relative_url.startswith("http") else BASE_URL + relative_url

    price = kart_fiyati_bul(category_html, product_id, product_url)
    if not price:
        print(f"UYARI: Fiyat bulunamadi | {code} | {name}")

    description, detail_images = detay_bilgileri(product_url)
    source_image = temizle(product.get("image"))
    images = []
    if source_image:
        images.append(source_image)
    for image in detail_images:
        if image not in images:
            images.append(image)

    main_category = ""
    if category_path:
        parts = [p.strip() for p in category_path.split(">") if p.strip()]
        if len(parts) >= 2:
            main_category = parts[1]
        elif parts:
            main_category = parts[0]

    xml_eleman(item, "id", product_id)
    xml_eleman(item, "code", code)
    xml_eleman(item, "label", name)
    xml_eleman(item, "stock", quantity)
    details = etree.SubElement(item, "details")
    details.text = etree.CDATA(description if description else f"<h2>{html.escape(name)}</h2>")
    xml_eleman(item, "currency", "TL")
    xml_eleman(item, "price1", price)
    xml_eleman(item, "tax", vat)
    xml_eleman(item, "barcode", barcode)
    xml_eleman(item, "brand", brand)
    xml_eleman(item, "mainCategory", main_category)
    xml_eleman(item, "category", category)
    xml_eleman(item, "subCategory", "")
    for i in range(5):
        xml_eleman(item, f"picture{i + 1}", images[i] if i < len(images) else "")
    xml_eleman(item, "sourceUrl", product_url)
    print(f"Eklendi: {code} | {name} | stok={quantity} | fiyat={price}")


def main():
    duna_giris_yap()

    category_urls = kategori_url_kesfet()

    root = etree.Element("root")
    seen = set()

    for category_index, category_url in enumerate(category_urls, 1):
        print(f"Kategori okunuyor ({category_index}/{len(category_urls)}): {category_url}")
        try:
            category_html = sayfa_getir(category_url)
        except Exception as exc:
            print("Kategori alinamadi:", exc)
            continue

        products = product_data_bul(category_html)
        print("Bu sayfadaki urun:", len(products))

        for product in products:
            product_id = temizle(product.get("id"))
            if not product_id or product_id in seen:
                continue

            seen.add(product_id)
            try:
                urun_xml_ekle(root, product, category_html)
            except Exception as exc:
                print("Urun islenirken hata:", product_id, exc)

            time.sleep(0.15)

    etree.ElementTree(root).write(
        "duna.xml", pretty_print=True, xml_declaration=True, encoding="UTF-8"
    )

    print("")
    print("duna.xml guncellendi.")
    print("Toplam kategori:", len(category_urls))
    print("Toplam benzersiz urun:", len(seen))


if __name__ == "__main__":
    main()
