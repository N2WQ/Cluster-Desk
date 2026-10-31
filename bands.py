"""Shared frequency groups for display and logger delivery."""

BANDS = [('160 m', 1800, 2000), ('80 m', 3500, 4000), ('60 m', 5195, 5450),
         ('40 m', 7000, 7300), ('30 m', 10100, 10150), ('20 m', 14000, 14350),
         ('17 m', 18068, 18168), ('15 m', 21000, 21450), ('12 m', 24890, 24990),
         ('10 m', 28000, 29700), ('6 m', 50000, 54000), ('4 m', 69887, 71500), ('2 m', 144000, 148000),
         ('1.25 m', 220000, 224000), ('70 cm', 420000, 450000), ('Microwave', 500000, 21000000)]

def band_for(frequency):
    try: value = float(frequency)
    except ValueError: return ''
    return next((name for name, low, high in BANDS if low <= value <= high), '')

SPOT_BANDS = frozenset(name for name, _, _ in BANDS) | {'Other'}
