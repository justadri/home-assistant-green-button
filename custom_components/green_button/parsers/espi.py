"""Module containing parsers for the Energy Services Provider Interface (ESPI) Atom feed defined by the North American Energy Standards Board."""
import datetime
from collections.abc import Callable
from typing import Final
from typing import TypeVar
from xml.etree import ElementTree as ET

from defusedxml import ElementTree as defusedET
from homeassistant.components import sensor
from homeassistant.const import UnitOfEnergy

from .. import model

T = TypeVar("T")

_NAMESPACE_MAP: Final = {
    "atom": "http://www.w3.org/2005/Atom",
    "espi": "http://naesb.org/espi",
}


_UOM_MAP: Final = {
    "72": UnitOfEnergy.WATT_HOUR,
}


_CURRENCY_MAP: Final = {
    "124": "CAD",
    "840": "USD",
}


_SERVICE_KIND: Final = {
    # 0 - Electricity.
    "0": sensor.SensorDeviceClass.ENERGY,
}


class EspiXmlParseError(ValueError):
    """Error when parsing ESPI XML."""


def _pretty_print(elem: ET.Element) -> str:
    return ET.tostring(elem, encoding="unicode")


def _parse_child_text(elem: ET.Element, xpath: str, parser: Callable[[str], T]) -> T:
    matches = elem.findall(xpath, _NAMESPACE_MAP)
    if len(matches) != 1:
        raise EspiXmlParseError(
            f"No path '{xpath}' found for entry:\n{_pretty_print(elem)}"
        )

    text = matches[0].text
    if text is None:
        raise EspiXmlParseError(
            f"Invalid value None at path {repr(xpath)} of entry:\n{_pretty_print(elem)}"
        )

    try:
        return parser(text)
    except ValueError as ex:
        raise EspiXmlParseError(
            f"Invalid value {repr(text)} at path '{xpath}' of entry:\n{_pretty_print(elem)}"
        ) from ex
    except KeyError as ex:  # For Mappings.
        raise EspiXmlParseError(
            f"Invalid value {repr(text)} at path '{xpath}' of entry:\n{_pretty_print(elem)}"
        ) from ex


def _parse_child_elems(
    elem: ET.Element, xpath: str, parser: Callable[[ET.Element], T]
) -> list[T]:
    out = []
    for match in elem.findall(xpath, _NAMESPACE_MAP):
        try:
            out.append(parser(match))
        except ValueError as ex:
            raise EspiXmlParseError(
                f"Invalid child at path '{xpath}' of entry:\n{_pretty_print(elem)}\nChild:\n{_pretty_print(match)}"
            ) from ex
    return out


def _to_utc_datetime(timestamp: str) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(int(timestamp), datetime.timezone.utc)


def _to_timedelta(duration: str) -> datetime.timedelta:
    return datetime.timedelta(seconds=int(duration))


class GreenButtonFeed:
    """A wrapper around a Green Button atom Feed XML element."""

    def __init__(self, xml: ET.Element) -> None:
        """Create a new instance."""
        self._xml = xml

    def find_entries(self, entry_type_tag: str) -> list["EspiEntry"]:
        """Find all atom entries whose root data tag has the specified name."""
        return [
            EspiEntry(self, elem, entry_type_tag)
            for elem in self._xml.findall(
                f"./atom:entry/atom:content/espi:{entry_type_tag}/../..",
                _NAMESPACE_MAP,
            )
        ]

    def to_usage_points(self) -> list[model.UsagePoint]:
        """Parse the feed into UsagePoints."""
        out = []
        for usage_point in self.find_entries("UsagePoint"):
            out.append(usage_point.to_usage_point())
        return out


class EspiEntry:
    """A wrapper around an atom Entry XML element."""

    def __init__(self, root: GreenButtonFeed, elem: ET.Element, type_tag: str) -> None:
        """Create a new instance."""
        self._root = root
        self._elem = elem
        self._type_tag = type_tag

    def _pretty_print(self) -> str:
        return _pretty_print(self._elem)

    def _find_link_hrefs(self, rel: str) -> list[str]:
        hrefs = []
        for link in self._elem.findall(f"./atom:link[@rel='{rel}']", _NAMESPACE_MAP):
            href = link.get("href")
            if href is not None:
                hrefs.append(href)
        return hrefs

    def find_self_href(self) -> str:
        """Find the entry's self HREF."""
        hrefs = self._find_link_hrefs("self")
        if not hrefs:
            raise EspiXmlParseError(f"No self link for entry:\n{self._pretty_print()}")
        return hrefs[0]

    def find_related_hrefs(self) -> list[str]:
        """Find the entry's related HREFs."""
        return self._find_link_hrefs("related")

    def parse_child_text(self, path: str, parser: Callable[[str], T]) -> T:
        """Parse the text of the element at the specified path."""
        xpath = f"./atom:content/espi:{self._type_tag}/{path}"
        return _parse_child_text(self._elem, xpath, parser)

    def parse_child_elems(
        self, path: str, parser: Callable[[ET.Element], T]
    ) -> list[T]:
        """Parse the *element* at the specified path."""
        xpath = f"./atom:content/espi:{self._type_tag}/{path}"
        return _parse_child_elems(self._elem, xpath, parser)

    def find_related_entries(
        self, related_entry_type_tag: str, parser: Callable[["EspiEntry"], T]
    ) -> list[T]:
        """Find all related entries whose root data tag has the specified name."""
        related_hrefs = self.find_related_hrefs()
        matches = []
        for related_entry in self._root.find_entries(related_entry_type_tag):
            related_entry_href = related_entry.find_self_href()
            if related_entry_href in related_hrefs:
                matches.append(parser(related_entry))
        return matches

    def find_first_related_entries(
        self, related_entry_type_tag: str, parser: Callable[["EspiEntry"], T]
    ) -> T:
        """Find the first related entry whose root data tag has the specified name."""
        matches = self.find_related_entries(related_entry_type_tag, parser)
        if not matches:
            raise EspiXmlParseError(
                f"No related entry with tag '{related_entry_type_tag}' found for entry:\n{self._pretty_print()}"
            )
        return matches[0]

    def create_interval_reading_parser(
        self, reading_type: model.ReadingType
    ) -> Callable[[ET.Element], model.IntervalReading]:
        """Create an IntervalReading parser for the ReadingType."""

        def parser(elem: ET.Element) -> model.IntervalReading:
            return model.IntervalReading(
                reading_type=reading_type,
                cost=0, #_parse_child_text(elem, "./espi:cost", int),
                start=_parse_child_text(
                    elem, "./espi:timePeriod/espi:start", _to_utc_datetime
                ),
                duration=_parse_child_text(
                    elem, "./espi:timePeriod/espi:duration", _to_timedelta
                ),
                value=_parse_child_text(elem, "./espi:value", int),
            )

        return parser

    def create_interval_block_parser(
        self, reading_type: model.ReadingType
    ) -> Callable[["EspiEntry"], model.IntervalBlock]:
        """Create an IntervalBlock parser for the ReadingType."""

        def parser(entry: EspiEntry) -> model.IntervalBlock:
            return model.IntervalBlock(
                id=entry.find_self_href(),
                reading_type=reading_type,
                start=entry.parse_child_text(
                    "espi:interval/espi:start", _to_utc_datetime
                ),
                duration=entry.parse_child_text(
                    "espi:interval/espi:duration", _to_timedelta
                ),
                interval_readings=entry.parse_child_elems(
                    "espi:IntervalReading",
                    entry.create_interval_reading_parser(reading_type),
                ),
            )

        return parser

    def to_reading_type(self) -> model.ReadingType:
        """Parse this entry as a ReadingType."""
        return model.ReadingType(
            id=self.find_self_href(),
             power_of_ten_multiplier=1,
            unit_of_measurement="kwh",
            currency=self.parse_child_text("espi:currency", _CURRENCY_MAP.__getitem__),
        )

    def to_meter_reading(self) -> model.MeterReading:
        """Parse this entry as a MeterReading."""
        reading_type = self.find_first_related_entries(
            "ReadingType",
            EspiEntry.to_reading_type,
        )
        return model.MeterReading(
            id=self.find_self_href(),
            reading_type=reading_type,
            interval_blocks=self.find_related_entries(
                "IntervalBlock",
                self.create_interval_block_parser(reading_type),
            ),
        )

    def to_usage_point(self) -> model.UsagePoint:
        """Parse this entry as a UsagePoint."""
        self_href = self.find_self_href()
        sensor_device_class = self.parse_child_text(
            "espi:ServiceCategory/espi:kind",
            _SERVICE_KIND.__getitem__,
        )
        return model.UsagePoint(
            id=self_href,
            sensor_device_class=sensor_device_class,
            meter_readings=self.find_related_entries(
                "MeterReading",
                EspiEntry.to_meter_reading,
            ),
        )


def parse_xml(value: str) -> list[model.UsagePoint]:
    """Parse an ESPI atom feed XML string."""
    try:
        root = defusedET.fromstring(value)
        return GreenButtonFeed(root).to_usage_points()
    except ET.ParseError as ex:
        raise EspiXmlParseError("Invalid XML.") from ex

def main():
    with open("test.xml", "r") as f:
        data = f.read()
    data = parse_xml(data)
    print(data)


if __name__ == '__main__':
    main()
