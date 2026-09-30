from scraper.config import Settings
from scraper.lims import LIMSCollector


def test_lims_ignores_nested_rows_and_keeps_test_details():
    html = b"""
    <table id='review_lab_list'><thead><tr>
      <th>S.No.</th><th>Lab Name</th><th>Osl Code</th><th>Indian Standard No.</th>
      <th>Product</th><th>Validity Date</th>
    </tr></thead><tbody><tr>
      <td>1</td><td>Example Lab (12345), Delhi</th><td>12345</th><td>IS 367 (1993)</td>
      <td>Electric kettles and jugs</td><td>31 Dec 2027
        <table><thead><tr><th>Clause No.</th><th>Testing Charges</th></tr></thead>
        <tbody><tr><td>6 (Rating)</td><td>1000</td></tr></tbody></table>
      </td>
    </tr></tbody></table>"""
    records = LIMSCollector(Settings(), None)._parse(html, "kettle", "https://lims.bis.gov.in/test")
    assert len(records) == 1
    assert records[0]["laboratory_id"] == "12345"
    assert records[0]["clause"] == ["6 (Rating)"]
    assert records[0]["test_method"][0]["testing charges"] == "1000"
