"""DXF unit declaration handling.

Isolated in its own module because unit mistakes silently corrupt every
residual number in a benchmark. Three rules:

  1. The unit is what the FILE DECLARES via $INSUNITS. Nothing is inferred
     from the magnitude of the coordinates. A drawing whose coordinates look
     like millimetres while declaring metres is reported as declaring metres.
  2. When units are not declared, residual reporting stays in raw CAD units.
     No mm or m label is ever produced. `UnitInfo.to_millimetres` returns
     None in that case and callers must not substitute a default.
  3. Unit conversion is a REPORTING concern only. It never enters the
     transform, the matching criterion, or any hypothesis decision.
"""

from typing import Dict, Optional

from .models import UNKNOWN_UNITS, UnitInfo

# AutoCAD $INSUNITS values. `metres_per_unit` is exact by definition.
INSUNITS: Dict[int, UnitInfo] = {
    # $INSUNITS=0 means unitless. It is a declaration of *no conversion*, not
    # a declaration that one CAD unit equals one metre.
    0: UnitInfo(0, "unitless", None),
    1: UnitInfo(1, "inches", 0.0254),
    2: UnitInfo(2, "feet", 0.3048),
    3: UnitInfo(3, "miles", 1609.344),
    4: UnitInfo(4, "millimetres", 0.001),
    5: UnitInfo(5, "centimetres", 0.01),
    6: UnitInfo(6, "metres", 1.0),
    7: UnitInfo(7, "kilometres", 1000.0),
    8: UnitInfo(8, "microinches", 2.54e-8),
    9: UnitInfo(9, "mils", 2.54e-5),
    10: UnitInfo(10, "yards", 0.9144),
    11: UnitInfo(11, "angstroms", 1e-10),
    12: UnitInfo(12, "nanometers", 1e-9),
    13: UnitInfo(13, "microns", 1e-6),
    14: UnitInfo(14, "decimetres", 0.1),
    15: UnitInfo(15, "decametres", 10.0),
    16: UnitInfo(16, "hectometres", 100.0),
    17: UnitInfo(17, "gigametres", 1e9),
    18: UnitInfo(18, "astronomical_units", 1.495978707e11),
    19: UnitInfo(19, "light_years", 9.4607304725808e15),
    20: UnitInfo(20, "parsecs", 3.0856775814913673e16),
}


def unit_from_insunits(raw: Optional[str]) -> UnitInfo:
    """Map a raw $INSUNITS value onto a UnitInfo.

    Absent or unparseable value -> UNKNOWN_UNITS, i.e. not declared. The
    caller must then report in raw CAD units.
    """
    if raw is None:
        return UNKNOWN_UNITS
    try:
        code = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return UNKNOWN_UNITS
    return INSUNITS.get(code, UnitInfo(code, "unknown_code_%d" % code, None))


def residual_unit_label(units: UnitInfo) -> str:
    """Label to attach to residual numbers."""
    if not units.declared:
        return "raw_cad_units"
    if units.metres_per_unit == 1.0:
        return "mm (from declared metres)"
    if units.metres_per_unit == 0.001:
        return "mm (declared millimetres)"
    return "mm (from declared %s)" % units.name


def cad_to_millimetres(value: float, units: UnitInfo) -> Optional[float]:
    """CAD native value -> millimetres, or None when units are not declared."""
    return units.to_millimetres(value)
