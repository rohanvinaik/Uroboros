def line_total(quantity, unit_price):
    return quantity * unit_price


def order_total(lines):
    return sum((line_total(quantity, unit_price) for quantity, unit_price in lines), 0.0)
