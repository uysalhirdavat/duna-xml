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

FALLBACK_CATEGORY_URLS = [
    BASE_URL + "/boya-tabancasi"
]

MAX_PAGE = 100


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


# =========================================================
# GENEL
# =========================================================

def temizle(value):
    if value is None:
        return ""
    return str(value).strip()


def tl_fiyat_cevir(value):
    """
    1.434,64 -> 1434.64
    789,05   -> 789.05
    789.05   -> 789.05
    """

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

    else:

        if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", value):
            value = value.replace(".", "")

    try:
        price = float(value)

        if price <= 0:
            return ""

        return f"{price:.2f}"

    except Exception:
        return ""


# =========================================================
# DUNA BAYİ GİRİŞİ
# =========================================================

def duna_giris_yap():

    username = os.environ.get("DUNA_USERNAME", "").strip()
    password = os.environ.get("DUNA_PASSWORD", "")

    if not username or not password:
        raise RuntimeError(
            "DUNA_USERNAME veya DUNA_PASSWORD GitHub Secret bulunamadi."
        )

    giris_sayfasi = BASE_URL + "/bayi-girisi-sayfasi"

    r = session.get(
        giris_sayfasi,
        timeout=30,
        allow_redirects=True
    )

    r.raise_for_status()

    login_url = (
        BASE_URL
        + "/srv/customer/signin/email/"
        + quote(username, safe="")
        + "?language=tr"
    )

    response = session.post(
        login_url,
        data={
            "password": password,
            "remember": "1"
        },
        headers={
            "Referer": giris_sayfasi,
            "Origin": BASE_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=30,
        allow_redirects=True,
    )

    response.raise_for_status()

    check = session.get(
        BASE_URL + "/uye-siparisleri",
        timeout=30,
        allow_redirects=True
    )

    check.raise_for_status()

    if "uye-siparisleri" not in check.url.lower():
        raise RuntimeError("Duna bayi girisi basarisiz.")

    print("Duna bayi girisi basarili.")


# =========================================================
# HTTP
# =========================================================

def sayfa_getir(url, referer=None, params=None):

    headers = {}

    if referer:
        headers["Referer"] = referer

    response = session.get(
        url,
        headers=headers or None,
        params=params,
        timeout=45,
        allow_redirects=True
    )

    response.raise_for_status()

    return response.text


# =========================================================
# PRODUCT_DATA
# =========================================================

def product_data_bul(html_text):

    products = []

    pattern = re.compile(
        r"PRODUCT_DATA\.push\("
        r"JSON\.parse\('((?:\\.|[^'])*)'\)"
        r"\)",
        re.DOTALL
    )

    for match in pattern.finditer(html_text):

        raw = match.group(1)

        try:

            decoded = bytes(
                raw,
                "utf-8"
            ).decode(
                "unicode_escape"
            )

            decoded = decoded.encode(
                "latin1"
            ).decode(
                "utf-8"
            )

        except Exception:

            decoded = (
                raw
                .replace("\\'", "'")
                .replace('\\"', '"')
            )

        try:

            data = json.loads(decoded)
            products.append(data)

        except Exception:
            continue

    return products


# =========================================================
# SAYFA TÜRÜ
# =========================================================

def sayfa_kategori_mi(html_text):

    patterns = [
        r"PAGE_TYPE\s*=\s*['\"]category['\"]",
        r"PAGE_TYPE['\"]?\s*:\s*['\"]category['\"]",
        r"['\"]PAGE_TYPE['\"]\s*=\s*['\"]category['\"]",
    ]

    for pattern in patterns:

        if re.search(
            pattern,
            html_text,
            re.I
        ):
            return True

    return False


# =========================================================
# KATEGORİ KEŞFİ
# =========================================================

def kategori_adayi_mi(url):

    parsed = urlparse(url)

    if (
        parsed.netloc
        and parsed.netloc not in {
            "www.duna.com.tr",
            "duna.com.tr"
        }
    ):
        return False

    path = parsed.path.rstrip("/")

    if not path:
        return False

    yasakli = (
        "/srv/",
        "/resources/",
        "/uploads/",
        "/uye-",
        "/bayi-",
        "/sepet",
        "/favori",
        "/iletisim",
        "/hakkimizda",
        "/blog",
        "/marka",
        "/search",
        "/arama",
        "/siparis",
        "/odeme",
        "/customer",
        "/account",
        "/login",
        "/signin",
    )

    if any(x in path.lower() for x in yasakli):
        return False

    if path.lower().endswith(
        (
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".svg",
            ".pdf",
            ".css",
            ".js",
            ".xml",
            ".ico",
        )
    ):
        return False

    return True


def kategori_url_kesfet():

    print(
        "Kategori listesi kesfediliyor:",
        CATALOG_URL
    )

    try:

        katalog_html = sayfa_getir(
            CATALOG_URL
        )

    except Exception as exc:

        print(
            "Kategori kesfi basarisiz:",
            exc
        )

        return FALLBACK_CATEGORY_URLS[:]

    soup = BeautifulSoup(
        katalog_html,
        "html.parser"
    )

    counts = Counter()

    for a in soup.find_all(
        "a",
        href=True
    ):

        href = html.unescape(
            a.get("href", "")
        ).strip()

        if not href:
            continue

        if href.startswith(
            (
                "#",
                "javascript:",
                "mailto:",
                "tel:"
            )
        ):
            continue

        absolute = urljoin(
            BASE_URL + "/",
            href
        )

        parsed = urlparse(
            absolute
        )

        clean = (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
            f"{parsed.path}"
        ).rstrip("/")

        if kategori_adayi_mi(clean):
            counts[clean] += 1

    adaylar = [
        url
        for url, count in counts.items()
        if count >= 2
    ]

    if CATALOG_URL not in adaylar:
        adaylar.insert(
            0,
            CATALOG_URL
        )

    for url in FALLBACK_CATEGORY_URLS:

        if url not in adaylar:
            adaylar.append(url)

    print(
        "Ham kategori adayi:",
        len(adaylar)
    )

    kategoriler = []

    for i, url in enumerate(
        adaylar,
        1
    ):

        try:

            if url == CATALOG_URL:
                page_html = katalog_html
            else:
                page_html = sayfa_getir(url)

            products = product_data_bul(
                page_html
            )

            # ÜRÜN DETAY SAYFALARINI KATEGORİ SANMA.
            if (
                products
                and sayfa_kategori_mi(page_html)
            ):

                kategoriler.append(url)

                print(
                    f"Kategori bulundu "
                    f"({i}/{len(adaylar)}): "
                    f"{url} | "
                    f"urun={len(products)}"
                )

        except Exception as exc:

            print(
                "Kategori adayi okunamadi:",
                url,
                exc
            )

        time.sleep(0.03)

    kategoriler = list(
        dict.fromkeys(kategoriler)
    )

    if not kategoriler:

        print(
            "Kategori bulunamadi, fallback kullaniliyor."
        )

        return FALLBACK_CATEGORY_URLS[:]

    print(
        "Dogrulanan kategori sayisi:",
        len(kategoriler)
    )

    return kategoriler


# =========================================================
# KATEGORİ LINK
# =========================================================

def kategori_link_bul(category_url):

    parsed = urlparse(
        category_url
    )

    return (
        parsed.path
        .strip("/")
        .split("/")[-1]
    )


# =========================================================
# SAYFALAMA
# =========================================================

def ikinci_sayfa_getir(
    category_url,
    link,
    pg
):

    """
    Duna tarayıcı isteğinde gördüğümüz yapı:

    products?link=el-aletleri&pg=2&language=tr

    Önce T-Soft servisinin trailing-slash sürümü denenir.

    Çalışmazsa tarayıcı adres çubuğunda gördüğümüz
    kategori?ps=2 yöntemi denenir.
    """

    service_urls = [
        BASE_URL + "/srv/service/product/products/",
        BASE_URL + "/srv/service/product/products",
    ]

    for service_url in service_urls:

        try:

            response = session.get(
                service_url,
                params={
                    "link": link,
                    "pg": pg,
                    "language": "tr"
                },
                headers={
                    "Referer": category_url,
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=45,
                allow_redirects=True
            )

            print(
                f"Sayfa istegi | "
                f"pg={pg} | "
                f"status={response.status_code} | "
                f"url={response.url}"
            )

            if response.status_code != 200:
                continue

            products = product_data_bul(
                response.text
            )

            if products:

                print(
                    f"Servis basarili | "
                    f"pg={pg} | "
                    f"urun={len(products)}"
                )

                return (
                    response.text,
                    products
                )

        except Exception as exc:

            print(
                "Servis hatasi:",
                service_url,
                exc
            )

    # -----------------------------------------------------
    # YEDEK YÖNTEM
    # Tarayıcı adresinde:
    # /el-aletleri?ps=2
    # görmüştük.
    # -----------------------------------------------------

    try:

        response = session.get(
            category_url,
            params={
                "ps": pg
            },
            headers={
                "Referer": category_url
            },
            timeout=45,
            allow_redirects=True
        )

        print(
            f"PS yedek istegi | "
            f"pg={pg} | "
            f"status={response.status_code} | "
            f"url={response.url}"
        )

        if response.status_code == 200:

            products = product_data_bul(
                response.text
            )

            if products:

                return (
                    response.text,
                    products
                )

    except Exception as exc:

        print(
            "PS yedek hatasi:",
            category_url,
            exc
        )

    return "", []


def kategori_tum_sayfalar(
    category_url,
    first_html
):

    first_products = product_data_bul(
        first_html
    )

    sayfalar = [
        (
            1,
            first_html,
            first_products
        )
    ]

    if not first_products:
        return sayfalar

    link = kategori_link_bul(
        category_url
    )

    if not link:
        return sayfalar

    print(
        f"Sayfalama basliyor | "
        f"link={link} | "
        f"ilk_sayfa={len(first_products)}"
    )

    seen_page_ids = {
        temizle(p.get("id"))
        for p in first_products
        if temizle(p.get("id"))
    }

    for pg in range(
        2,
        MAX_PAGE + 1
    ):

        page_html, products = (
            ikinci_sayfa_getir(
                category_url,
                link,
                pg
            )
        )

        if not products:

            print(
                f"Sayfalama bitti | "
                f"{category_url} | "
                f"son_sayfa={pg - 1}"
            )

            break

        ids = {
            temizle(p.get("id"))
            for p in products
            if temizle(p.get("id"))
        }

        yeni_ids = (
            ids -
            seen_page_ids
        )

        if not yeni_ids:

            print(
                f"Sayfalama tekrar etti | "
                f"{category_url} | "
                f"pg={pg}"
            )

            break

        sayfalar.append(
            (
                pg,
                page_html,
                products
            )
        )

        seen_page_ids.update(ids)

        print(
            f"Sayfa {pg}: "
            f"urun={len(products)} | "
            f"yeni={len(yeni_ids)} | "
            f"kumulatif={len(seen_page_ids)}"
        )

        time.sleep(0.08)

    return sayfalar


# =========================================================
# FİYAT
# =========================================================

def kart_fiyati_bul(
    html_text,
    product_id,
    product_url=""
):

    soup = BeautifulSoup(
        html_text,
        "html.parser"
    )

    card = soup.find(
        attrs={
            "data-id": str(product_id)
        }
    )

    if card:

        # Duna'nın gerçek bayi satış fiyatı.
        price_node = card.select_one(
            '[data-toggle="price-sell"]'
        )

        if price_node:

            fiyat = tl_fiyat_cevir(
                price_node.get_text(
                    " ",
                    strip=True
                )
            )

            if fiyat:
                return fiyat

        # YEDEK
        matches = re.findall(
            r"([0-9]{1,3}"
            r"(?:\.[0-9]{3})*"
            r"(?:,[0-9]{2})"
            r"|[0-9]+,[0-9]{2})"
            r"\s*(?:TL|₺)",
            card.get_text(
                " ",
                strip=True
            ),
            re.I
        )

        if matches:

            fiyat = tl_fiyat_cevir(
                matches[-1]
            )

            if fiyat:
                return fiyat

    # Ürün detay sayfasından yedek fiyat.
    if product_url:

        try:

            detail_html = sayfa_getir(
                product_url
            )

            detail_soup = BeautifulSoup(
                detail_html,
                "html.parser"
            )

            price_node = detail_soup.select_one(
                '[data-toggle="price-sell"]'
            )

            if price_node:

                fiyat = tl_fiyat_cevir(
                    price_node.get_text(
                        " ",
                        strip=True
                    )
                )

                if fiyat:
                    return fiyat

        except Exception as exc:

            print(
                "Fiyat detay hatasi:",
                product_url,
                exc
            )

    return ""


# =========================================================
# AÇIKLAMA + GÖRSELLER
# =========================================================

def detay_bilgileri(url):

    """
    Duna PRODUCT_DATA içindeki ürün URL'si bazı ürünlerde İngilizce slug'a gider.
    Ürün sayfasındaki resmi <link rel="alternate" hreflang="tr"> adresini bulup
    Türkçe sayfayı açar. İsim ve açıklama Duna'dan geldiği haliyle alınır.
    Hiçbir otomatik çeviri yapılmaz.
    """

    try:
        first_html = sayfa_getir(
            url,
            params={"language": "tr"}
        )
    except Exception as exc:
        print(
            "Detay sayfasi alinamadi:",
            url,
            exc
        )
        return "", "", [], url

    first_soup = BeautifulSoup(
        first_html,
        "html.parser"
    )

    turkish_url = url

    # Duna'nın kendi çok-dilli yapısındaki resmi Türkçe URL.
    for link in first_soup.find_all(
        "link",
        attrs={"rel": "alternate"}
    ):
        hreflang = temizle(
            link.get("hreflang")
        ).lower()

        href = temizle(
            link.get("href")
        )

        if hreflang in {"tr", "tr-tr"} and href:
            turkish_url = urljoin(
                BASE_URL,
                html.unescape(href)
            )
            break

    # İlk URL İngilizce slug ise Türkçe alternate sayfayı ayrıca aç.
    if turkish_url.rstrip("/") != url.rstrip("/"):
        try:
            turkish_html = sayfa_getir(
                turkish_url,
                referer=url
            )
            soup = BeautifulSoup(
                turkish_html,
                "html.parser"
            )
        except Exception as exc:
            print(
                "Turkce detay sayfasi alinamadi, ilk sayfa kullaniliyor:",
                turkish_url,
                exc
            )
            soup = first_soup
            turkish_url = url
    else:
        soup = first_soup

    # Ürün adını Duna'nın Türkçe ürün detay sayfasından olduğu gibi al.
    detail_name = ""

    for selector in [
        "#product-title",
        "h1#product-title",
        "h1[itemprop='name']",
        "h1.product-title",
        "h1"
    ]:
        node = soup.select_one(selector)

        if node:
            candidate = temizle(
                node.get_text(
                    " ",
                    strip=True
                )
            )

            if candidate:
                detail_name = candidate
                break

    # H1 bulunamazsa Duna'nın kendi og:title / title alanını kullan.
    if not detail_name:
        og_title = soup.select_one(
            'meta[property="og:title"]'
        )

        if og_title:
            detail_name = temizle(
                og_title.get("content")
            )

    if not detail_name and soup.title:
        detail_name = temizle(
            soup.title.get_text(
                " ",
                strip=True
            )
        )

    # Açıklama ve teknik özellikleri Duna'nın Türkçe sayfasından olduğu gibi al.
    description = ""

    for selector in [
        "#product-features",
        ".product-detail-description",
        ".product-description",
        ".productDetailDescription",
        "[itemprop='description']",
    ]:
        node = soup.select_one(selector)

        if (
            node
            and len(
                node.get_text(
                    " ",
                    strip=True
                )
            ) > 30
        ):
            description = str(node)
            break

    # Detay HTML alanı yoksa Duna'nın kendi meta açıklamasını yedek olarak al.
    if not description:
        meta_description = soup.select_one(
            'meta[name="description"]'
        )

        if meta_description:
            meta_text = temizle(
                meta_description.get("content")
            )

            if meta_text:
                description = (
                    "<p>"
                    + html.escape(meta_text)
                    + "</p>"
                )

    images = []

    for img in soup.find_all(
        "img"
    ):
        image_src = (
            img.get("data-src")
            or img.get("data-original")
            or img.get("src")
        )

        if not image_src:
            continue

        image_src = html.unescape(
            image_src
        )

        if image_src.startswith("//"):
            image_src = (
                "https:"
                + image_src
            )

        elif image_src.startswith("/"):
            image_src = (
                BASE_URL
                + image_src
            )

        if (
            "duna.com.tr" in image_src
            and (
                "-O." in image_src
                or "-B." in image_src
            )
            and image_src not in images
        ):
            images.append(
                image_src
            )

    return (
        detail_name,
        description,
        images[:5],
        turkish_url
    )


# =========================================================
# XML
# =========================================================

def xml_eleman(
    parent,
    tag,
    value=""
):

    node = etree.SubElement(
        parent,
        tag
    )

    if value is not None:
        node.text = temizle(value)

    return node


def urun_xml_ekle(
    root,
    product,
    page_html
):

    item = etree.SubElement(
        root,
        "item"
    )

    product_id = temizle(
        product.get("id")
    )

    code = temizle(
        product.get("code")
        or product.get("supplier_code")
    )

    name = temizle(
        product.get("name")
    )

    barcode = temizle(
        product.get("barcode")
    )

    brand = temizle(
        product.get("brand")
    )

    quantity = temizle(
        product.get(
            "quantity",
            "0"
        )
    )

    vat = temizle(
        product.get(
            "vat",
            "20"
        )
    )

    category = temizle(
        product.get("category")
    )

    category_path = temizle(
        product.get(
            "category_path"
        )
    )

    relative_url = temizle(
        product.get("url")
    )

    if relative_url.startswith("http"):

        product_url = relative_url

    else:

        product_url = (
            BASE_URL +
            relative_url
        )

    # FİYAT
    price = kart_fiyati_bul(
        page_html,
        product_id,
        product_url
    )

    if not price:

        print(
            f"UYARI: Fiyat bulunamadi | "
            f"{code} | "
            f"{name}"
        )

    # Duna'nın resmi Türkçe detay URL'sinden isim + açıklama + görseller.
    detail_name, description, detail_images, turkish_product_url = (
        detay_bilgileri(
            product_url
        )
    )

    # Türkçe detay sayfasında isim bulunduysa aynen kullan.
    # Bulunamazsa PRODUCT_DATA adı yedek olarak korunur; çeviri yapılmaz.
    if detail_name:
        name = detail_name

    images = []

    source_image = temizle(
        product.get("image")
    )

    if source_image:
        images.append(
            source_image
        )

    for image in detail_images:

        if image not in images:
            images.append(
                image
            )

    # KATEGORİLER
    main_category = ""
    sub_category = ""

    if category_path:

        parts = [
            p.strip()
            for p in category_path.split(">")
            if p.strip()
        ]

        # Tüm Ürün Grupları > El Aletleri > Pense >
        if len(parts) >= 2:
            main_category = parts[1]

        if len(parts) >= 3:
            sub_category = parts[2]

    xml_eleman(
        item,
        "id",
        product_id
    )

    xml_eleman(
        item,
        "code",
        code
    )

    xml_eleman(
        item,
        "label",
        name
    )

    xml_eleman(
        item,
        "stock",
        quantity
    )

    details = etree.SubElement(
        item,
        "details"
    )

    if description:

        details.text = etree.CDATA(
            description
        )

    else:

        details.text = etree.CDATA(
            f"<h2>{html.escape(name)}</h2>"
        )

    xml_eleman(
        item,
        "currency",
        "TL"
    )

    xml_eleman(
        item,
        "price1",
        price
    )

    xml_eleman(
        item,
        "tax",
        vat
    )

    xml_eleman(
        item,
        "barcode",
        barcode
    )

    xml_eleman(
        item,
        "brand",
        brand
    )

    xml_eleman(
        item,
        "mainCategory",
        main_category
    )

    xml_eleman(
        item,
        "category",
        category
    )

    xml_eleman(
        item,
        "subCategory",
        sub_category
    )

    for i in range(5):

        value = (
            images[i]
            if i < len(images)
            else ""
        )

        xml_eleman(
            item,
            f"picture{i + 1}",
            value
        )

    xml_eleman(
        item,
        "sourceUrl",
        turkish_product_url
    )

    print(
        f"Eklendi: "
        f"{code} | "
        f"{name} | "
        f"stok={quantity} | "
        f"fiyat={price}"
    )


# =========================================================
# ANA ÇALIŞMA
# =========================================================

def main():

    duna_giris_yap()

    category_urls = (
        kategori_url_kesfet()
    )

    root = etree.Element(
        "root"
    )

    # Aynı ürün farklı kategorilerde görünse de
    # XML'e yalnızca bir defa yaz.
    seen = set()

    toplam_sayfa = 0

    for category_index, category_url in enumerate(
        category_urls,
        1
    ):

        print("")
        print(
            f"Kategori okunuyor "
            f"({category_index}/"
            f"{len(category_urls)}): "
            f"{category_url}"
        )

        try:

            first_html = sayfa_getir(
                category_url
            )

        except Exception as exc:

            print(
                "Kategori alinamadi:",
                exc
            )

            continue

        sayfalar = (
            kategori_tum_sayfalar(
                category_url,
                first_html
            )
        )

        toplam_sayfa += len(
            sayfalar
        )

        print(
            "Kategori toplam sayfa:",
            len(sayfalar)
        )

        for (
            pg,
            page_html,
            products
        ) in sayfalar:

            print(
                f"Isleniyor "
                f"pg={pg} | "
                f"urun={len(products)}"
            )

            for product in products:

                product_id = temizle(
                    product.get("id")
                )

                if not product_id:
                    continue

                if product_id in seen:
                    continue

                seen.add(
                    product_id
                )

                try:

                    urun_xml_ekle(
                        root,
                        product,
                        page_html
                    )

                except Exception as exc:

                    print(
                        "Urun islenirken hata:",
                        product_id,
                        exc
                    )

                time.sleep(0.20)

    etree.ElementTree(
        root
    ).write(
        "duna.xml",
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8"
    )

    print("")
    print(
        "================================="
    )

    print(
        "duna.xml guncellendi."
    )

    print(
        "Toplam kategori:",
        len(category_urls)
    )

    print(
        "Toplam kategori sayfasi:",
        toplam_sayfa
    )

    print(
        "Toplam benzersiz urun:",
        len(seen)
    )

    print(
        "================================="
    )


if __name__ == "__main__":
    main()
