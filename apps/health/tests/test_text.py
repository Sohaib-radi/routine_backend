import pytest

from apps.health.text import normalize


@pytest.mark.parametrize(('text', 'expected'), [
    ('طعمية', 'طعميه'),           # ة → ه
    ('طعميه', 'طعميه'),
    ('أرز', 'ارز'),               # أ → ا
    ('إفطار', 'افطار'),           # إ → ا
    ('آيس كريم', 'ايس كريم'),     # آ → ا
    ('مستشفى', 'مستشفي'),         # ى → ي
    ('فُول مُدَمَّس', 'فول مدمس'),  # tashkeel removed
    ('كشـــري', 'كشري'),          # tatweel removed
    ('Ta\'meya', 'tameya'),
    ('ta’meya', 'tameya'),
    ('Pâtes', 'pates'),
    ('Thé  au   sucre', 'the au sucre'),
    ('Crème brûlée', 'creme brulee'),
    ('cous-cous', 'couscous'),
    ('', ''),
])
def test_normalize(text, expected):
    assert normalize(text) == expected


def test_same_food_written_differently_matches():
    assert normalize('طَعْمِيَّة') == normalize('طعميه')
    assert normalize('آكلة') == normalize('اكله')


def test_other_arabic_letters_are_kept():
    """Only the listed letters are unified: ؤ and ئ stay as they are."""
    assert normalize('مؤكد شئ') == 'مؤكد شئ'
