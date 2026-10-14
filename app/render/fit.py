EMU_PER_PT = 12700
AVG_CHAR_EM = 0.50
LINE_SPACING = 1.2


def est_lines(text: str, size_pt: float, width_emu: int) -> int:
    per_line = max(1, int((width_emu / EMU_PER_PT) / (size_pt * AVG_CHAR_EM)))
    total = 0
    for para in text.split("\n"):
        lines, cur = 1, 0
        for word in para.split():
            n = len(word)
            if n > per_line:
                if cur:
                    lines += 1
                lines += (n - 1) // per_line
                cur = n % per_line or per_line
            elif cur == 0:
                cur = n
            elif cur + 1 + n <= per_line:
                cur += 1 + n
            else:
                lines += 1
                cur = n
        total += lines
    return total


def line_height(size_pt: float) -> int:
    return int(size_pt * LINE_SPACING * EMU_PER_PT)


def text_height(text: str, size_pt: float, width_emu: int) -> int:
    return est_lines(text, size_pt, width_emu) * line_height(size_pt)


def choose_size(sizes_desc, fits) -> float:
    """Return the first (largest) size for which fits(size) is True,
    or the smallest size if none fit."""
    for s in sizes_desc:
        if fits(s):
            return s
    return sizes_desc[-1]
