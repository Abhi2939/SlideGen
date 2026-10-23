"""Small, explicitly curated public-download fallback; never scrape/search HTML.

Pexels License: https://www.pexels.com/license/
These are illustrative stock photos, not company products or endorsements.
Only metadata is bundled; image bytes use the normal bounded disk cache.
Source pages and photographer credits were checked on 2026-10-24.
"""

PHOTO_CATALOG = {
    "cosmetics makeup": (
        (
            "7256097/pexels-photo-7256097.jpeg",
            "close-up-shot-of-cosmetic-products-on-a-white-surface-7256097",
            "DS stories",
        ),
        (
            "8558440/pexels-photo-8558440.jpeg",
            "cosmetic-products-and-makeup-brushes-8558440",
            "Nataliya Vaitkevich",
        ),
        (
            "37356602/pexels-photo-37356602/free-photo-of-flat-lay-of-makeup-products-on-white-background.jpeg",
            "flat-lay-of-makeup-products-on-white-background-37356602",
            "Letícia Alvares",
        ),
        (
            "12955707/pexels-photo-12955707.jpeg",
            "a-flatlay-of-cosmetics-12955707",
            "Atlantic Ambience",
        ),
        ("3552894/pexels-photo-3552894.jpeg", "photo-of-beauty-products-3552894", "Nothing Ahead"),
    ),
    "financial technology": (
        (
            "18111273/pexels-photo-18111273/free-photo-of-glass-wall-of-modern-office-building.jpeg",
            "glass-wall-of-modern-office-building-18111273",
            "Joaquin Carfagna",
        ),
        (
            "18468708/pexels-photo-18468708/free-photo-of-glass-facades-of-modern-skyscrapers.jpeg",
            "glass-facades-of-modern-skyscrapers-18468708",
            "Artem Zhukov",
        ),
        (
            "36392985/pexels-photo-36392985.jpeg",
            "modern-skyscrapers-in-singapore-s-financial-district-36392985",
            "Jess Chen",
        ),
    ),
    "education learning": (
        (
            "5212695/pexels-photo-5212695.jpeg",
            "a-two-girls-using-laptop-with-classmates-5212695",
            "Max Fischer",
        ),
        (
            "8199761/pexels-photo-8199761.jpeg",
            "students-studying-together-while-looking-at-the-screen-of-a-laptop-8199761",
            "Yan Krukau",
        ),
        (
            "6147267/pexels-photo-6147267.jpeg",
            "young-students-working-on-laptop-while-preparing-for-exam-in-campus-6147267",
            "Keira Burton",
        ),
    ),
}


def curated_photo(query, variant):
    choices = PHOTO_CATALOG.get(query, ())
    if not 0 <= variant < len(choices):
        return None
    image, page, photographer = choices[variant]
    return {
        "src": {
            "large": f"https://images.pexels.com/photos/{image}?auto=compress&dpr=1&h=750&w=1260"
        },
        "url": f"https://www.pexels.com/photo/{page}/",
        "photographer": photographer,
    }
