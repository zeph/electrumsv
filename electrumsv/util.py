# Maybe near format_satoshis
def format_satoshis(amount, is_diff=False, num_zeros=0, decimal_point=8, whitespaces=False,
        precision=None):
    if amount is None:
        return 'unknown'
    if precision is None:
        precision = decimal_point
    # Treat values below dust threshold as zero for display purposes.
    if abs(amount) < dust_threshold(Net):
        #amount = 0
        pass # Let the number display correctly
    s = "{0:.%df}" % precision
    s = s.format(Decimal(amount) / COIN)
    if num_zeros > 0:
        grouping = ' '.join(['0'] * num_zeros)
        s = s.replace('0', grouping)
    if whitespaces:
        s += ' ' * (decimal_point - precision)
        s = ' '.join(split_by_cycles(s[::-1], 3))[::-1]
    sign = ''
    if is_diff:
        diff_amount = Decimal(amount) / COIN
        sign = '+' if diff_amount > 0 else '-'
        s = s.lstrip('-')
    return sign + s

# ... rest of util.py ... 