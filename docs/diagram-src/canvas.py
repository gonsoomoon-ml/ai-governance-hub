"""폭 인식(한글=2칸) ASCII 캔버스 — 박스/레일 정렬을 좌표로 보장한다."""
import unicodedata

WIDE = ('W', 'F')


def cw(ch):
    return 2 if unicodedata.east_asian_width(ch) in WIDE else 1


def dw(s):
    return sum(cw(c) for c in s)


class Canvas:
    FILL = '\0'

    def __init__(self, w=100, h=140):
        self.w, self.h = w, h
        self.g = [[' '] * w for _ in range(h)]

    def put(self, y, x, s):
        """표시 열 x 에서 문자열 s 를 찍는다. 광폭 문자는 2칸 점유."""
        for ch in s:
            n = cw(ch)
            if 0 <= x < self.w:
                self.g[y][x] = ch
                for k in range(1, n):
                    if x + k < self.w:
                        self.g[y][x + k] = self.FILL
            x += n
        return x

    def hl(self, y, x1, x2, ch='─'):
        for x in range(x1, x2 + 1):
            self.g[y][x] = ch

    def vl(self, x, y1, y2, ch='│'):
        for y in range(y1, y2 + 1):
            self.g[y][x] = ch

    def box(self, y, x, h, w, title='', tl='┌', tr='┐', bl='└', br='┘',
            hz='─', vt='│', title_x=None):
        """(y,x) 좌상단, h 행 / w 표시열(테두리 포함) 박스."""
        x2, y2 = x + w - 1, y + h - 1
        self.hl(y, x, x2, hz)
        self.hl(y2, x, x2, hz)
        self.vl(x, y, y2, vt)
        self.vl(x2, y, y2, vt)
        self.g[y][x], self.g[y][x2] = tl, tr
        self.g[y2][x], self.g[y2][x2] = bl, br
        if title:
            self.put(y, x + 2 if title_x is None else title_x, ' ' + title + ' ')
        return self

    def txt(self, y, x, s):
        self.put(y, x, s)
        return self

    def rows(self, y, x, lines):
        for i, line in enumerate(lines):
            self.put(y + i, x, line)
        return self

    def render(self):
        out = []
        for row in self.g:
            out.append(''.join(c for c in row if c != self.FILL).rstrip())
        while out and not out[-1]:
            out.pop()
        return '\n'.join(out)


def audit(text, label=''):
    """렌더 결과 검증: 최대폭 + 세로선(│) 열이 연속인지 리포트."""
    lines = text.split('\n')
    print(f'--- audit {label} ---')
    print(f'lines={len(lines)}  max_display_width={max(dw(l) for l in lines)}')
    bad = []
    for i, l in enumerate(lines):
        # 렌더 후 문자열을 다시 표시열로 매핑
        col, cols = 0, []
        for ch in l:
            if ch in '│├┤┬┴┼╪╬║':
                cols.append(col)
            col += cw(ch)
        bad.append((i + 1, cols))
    return bad


# ---- 헬퍼 (aws-draw-ascii-diagram): 연결선 산수를 없앤다 ------------------------
def harrow(c, y, x_wall_left, x_wall_right, label='', reverse=False):
    """가로 연결선. 왼쪽 박스의 오른쪽 벽(x_wall_left)에서 오른쪽 박스의 왼쪽 벽(x_wall_right)까지.
    reverse=False: ├─label─▶│   reverse=True: │◀─label─┤   (화살촉은 벽 앞, 벽 문자는 유지)
    label 은 ASCII 만. 남는 칸은 ─ 로 채운다."""
    gap = x_wall_right - x_wall_left - 1
    if label and dw(label) > gap - 3:
        raise ValueError(f"harrow: label {label!r}({dw(label)}) 가 간격 {gap} 에 안 맞음 — 오른쪽 박스를 x+{dw(label)+3-gap} 이상 옮기거나 라벨을 줄여라")
    body = ('─' + label + '─' * gap)[:gap - 1] if label else '─' * (gap - 1)
    if not reverse:
        c.g[y][x_wall_left] = '├'
        c.put(y, x_wall_left + 1, body); c.g[y][x_wall_right - 1] = '▶'
    else:
        c.g[y][x_wall_right] = '┤'
        c.put(y, x_wall_left + 2, body); c.g[y][x_wall_left + 1] = '◀'


def vrail(c, x, y_from_wall, y_to_wall, labels=()):
    """세로 흐름. 위 박스 아랫테두리(y_from_wall)에 ┬, 아래 박스 윗테두리(y_to_wall) 바로 위 행에 ▼,
    사이는 │. labels 는 레일 오른쪽(x+2) 에 한 줄씩 — 한글 가능(마지막 벽 뒤)."""
    need = 2 + len(labels)
    if y_to_wall - y_from_wall < need:
        raise ValueError(f"vrail: 행 부족 — 아래 박스 윗선 y={y_to_wall} 는 최소 y={y_from_wall + need} 여야 (라벨 {len(labels)}줄)")
    c.g[y_from_wall][x] = '┬'
    for i, y in enumerate(range(y_from_wall + 1, y_to_wall - 1)):
        c.g[y][x] = '│'
        if i < len(labels): c.put(y, x + 2, labels[i])
    c.g[y_to_wall - 1][x] = '▼'


def boxed(c, y, x, h, w, title, lines=(), title_x=None):
    """box + rows 를 한 번에. 내용이 벽을 넘으면 즉시 실패(벽이 지워지는 사고 방지). h 는 테두리 포함, 최소 2."""
    inner = w - 4
    for l in lines:
        if dw(l) > inner:
            raise ValueError(f"boxed: {l!r}({dw(l)}) 가 박스 안폭 {inner} 초과 — 박스를 넓히거나 줄을 나눠라 (title {title!r})")
    if len(lines) > h - 2:
        raise ValueError(f"boxed: 줄 {len(lines)} > h-2={h-2} (title {title!r})")
    c.box(y, x, h, w, title, title_x=(x + 2 if title_x is None else title_x))
    c.rows(y + 1, x + 2, list(lines))
    return (y, x, h, w)


def fanout(c, x_from, y_from_wall, targets_x, y_to_wall, label=''):
    """1 → N. 위 박스 아랫테두리(x_from,y_from_wall) 의 ┬ 에서 내려와 버스 행에서 갈라져
    각 targets_x 로 내려가 아래 박스들 윗선 '위' 행에 ▼. 버스 행 = y_to_wall-2 (최소 3행 필요)."""
    if y_to_wall - y_from_wall < 3:
        raise ValueError("fanout: 최소 3행 필요 (┬ / 버스 / ▼)")
    y_bus = y_to_wall - 2
    c.g[y_from_wall][x_from] = '┬'
    for y in range(y_from_wall + 1, y_bus): c.g[y][x_from] = '│'
    if label: c.put(y_from_wall + 1, x_from + 2, label)
    xs = sorted(set(targets_x)); lo, hi = min(xs + [x_from]), max(xs + [x_from])
    c.hl(y_bus, lo, hi)
    for x in xs:
        c.g[y_bus][x] = '┌' if x == lo else ('┐' if x == hi else '┬')
        c.g[y_bus + 1][x] = '▼'
    if x_from in xs:
        c.g[y_bus][x_from] = '┼' if lo < x_from < hi else ('├' if x_from == lo else '┤')
    else:
        c.g[y_bus][x_from] = '┴' if lo < x_from < hi else ('└' if x_from == lo else '┘')
        if x_from == lo: c.g[y_bus][x_from] = '└'
        if x_from == hi: c.g[y_bus][x_from] = '┘'


def fanin(c, sources_x, y_from_wall, x_to, y_to_wall, label=''):
    """N → 1. 위 박스들 아랫테두리(y_from_wall) 의 ┬ 들에서 내려와 버스 행에서 합쳐져 x_to 로 내려간다.
    버스 행 = y_from_wall+2, 그 아래 │ … ▼(y_to_wall-1). 최소 4행."""
    if y_to_wall - y_from_wall < 4:
        raise ValueError("fanin: 최소 4행 필요 (┬ / │ / 버스 / … ▼)")
    y_bus = y_from_wall + 2
    xs = sorted(set(sources_x)); lo, hi = min(xs + [x_to]), max(xs + [x_to])
    for x in xs:
        c.g[y_from_wall][x] = '┬'; c.g[y_from_wall + 1][x] = '│'
    c.hl(y_bus, lo, hi)
    for x in xs:
        c.g[y_bus][x] = '└' if x == lo else ('┘' if x == hi else '┴')
    if x_to in xs:
        c.g[y_bus][x_to] = '┼' if lo < x_to < hi else ('├' if x_to == lo else '┤')
    else:
        c.g[y_bus][x_to] = '┬' if lo < x_to < hi else ('┌' if x_to == lo else '┐')
    for y in range(y_bus + 1, y_to_wall - 1): c.g[y][x_to] = '│'
    if label: c.put(y_bus + 1, x_to + 2, label)
    c.g[y_to_wall - 1][x_to] = '▼'


def sideloop(c, x_rail, y1, y2, label='retry x1', side=3):
    """레일 옆에 되돌아가는 루프 표시(재시도 등). y1<y2 인 레일 구간 옆(x_rail+side)에 ▲│ 를 세우고 라벨(ASCII)."""
    x = x_rail + side
    c.g[y1][x] = '▲'
    for y in range(y1 + 1, y2 + 1): c.g[y][x] = '│'
    c.put(y1 + 1, x + 2, label)
