PASS_MARK = 50


def passes(score):
    return score >= PASS_MARK


def pass_rate(scores):
    if not scores:
        return 0.0
    return sum(1 for score in scores if passes(score)) / len(scores)
