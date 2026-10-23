"""Shared 12-column slide grid. All coordinates here are in inches."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Grid:
    width: float = 13.333
    height: float = 7.5
    margin: float = 0.6
    gutter: float = 0.25
    columns: int = 12
    baseline: float = 0.1

    @property
    def column(self):
        return (self.width - 2 * self.margin - (self.columns - 1) * self.gutter) / self.columns

    def x(self, column):
        return self.margin + column * (self.column + self.gutter)

    def span(self, columns):
        return columns * self.column + (columns - 1) * self.gutter

    def y(self, value):
        return round((value - self.margin) / self.baseline) * self.baseline + self.margin


GRID = Grid()
