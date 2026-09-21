"""Offline rendered contract checks."""
import re
from decimal import Decimal
from html.parser import HTMLParser
import pytest
from tests.unit.test_final_offline import agent, inputs
from services.pdf_generator import _render_html, _report_money
pytestmark = pytest.mark.usefixtures('block_network')
class Visible(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.hidden=False
    def handle_starttag(self,tag,attrs):
        if tag in ('style','script'): self.hidden=True
    def handle_endtag(self,tag):
        if tag in ('style','script'): self.hidden=False
    def handle_data(self,data):
        if not self.hidden: self.parts.append(data)
def clean(html):
    p=Visible(); p.feed(html); text=' '.join(p.parts)
    assert not re.search(r'\b(?:NaN|Infinity|None|null|undefined)\b|\$N/A',text)
    return text
def amounts(html):
    return [Decimal(x.replace(',','')) for x in re.findall(r'\$([\d,]+\.\d{2})',html)]
@pytest.mark.asyncio
@pytest.mark.parametrize('client',[True,False])
async def test_full_render(agent,inputs,client):
    final=await agent.run('offline-pdf',inputs)
    root=agent.firestore.update_estimate.await_args.args[1]; root['finalOutput']=final
    html=_render_html(root,{'cost_estimate':root['cost_breakdown'],'risk_analysis':root['risk_analysis']},
                      ['executive_summary','cost_breakdown','risk_analysis'],client,'offline')
    text=clean(html)
    table=re.search(r'<table class="monetary-summary">(.*?)</table>',html,re.S)[1]
    assert amounts(table)==list(map(Decimal,['27942.71','3514.03','31456.74']))
    assert sum(amounts(table)[:2])==amounts(table)[2]
    assert table.count('Selected Contingency')==1
    assert 'Cabinetry & Millwork' not in text and 'Overhead & Profit' not in text
    assert '12.58%' in text
    if not client:
        components=re.search(r'<table class="base-components">(.*?)</table>',html,re.S)[1]
        assert sum(amounts(components))==Decimal('27942.71')
        assert '$1,055.40' in components
        assert 'Overhead</td><td class="currency">$2,216.34' in components
        assert 'Profit</td><td class="currency">$2,437.97' in components
        for v in ('$29,283.60','$32,797.63','$35,067.77'): assert v in text
@pytest.mark.parametrize('client',[True,False])
def test_legacy_precedence(client):
    data={'totalCost':32797.63,'finalEstimate':999,'finalOutput':{'finalEstimate':31456.74,'baseEstimate':27942.71,'contingency':3514.03}}
    html=_render_html(data,{},['cost_breakdown'],client,'offline')
    assert '$31,456.74' in clean(html)
    assert '$32,797.63' not in html and '$999.00' not in html
@pytest.mark.parametrize('client',[True,False])
@pytest.mark.parametrize('data',[{}, {'totalCost':50}, {'squareFootage':196}, {'finalEstimate':0,'totalCost':50}, {'finalEstimate':float('nan'),'p50':'Infinity'}])
def test_missing(data,client):
    html=_render_html(data,{},['executive_summary','cost_breakdown','risk_analysis'],client,'offline')
    clean(html)
    if not client: assert 'Risk percentiles unavailable' in html
    assert '$55.00' not in html
    if data.get('finalEstimate')==0: assert '$0.00' in html
@pytest.mark.parametrize('impact,expected',[('1234.50','$1,234.50'),('high','N/A'),(None,'N/A')])
def test_risk_strings(impact,expected):
    html=_render_html({'p50':'1','p80':'2','p90':'3'},{'risk_analysis':{'top_risks':[{'item':'Risk','impact':impact,'probability':'0.25','sensitivity':'0.4'}]}},['risk_analysis'],False,'offline')
    assert expected in clean(html) and '25%' in html and '0.40' in html
def test_mapping():
    r=_report_money({'totalCost':32797.63,'finalOutput':{'executiveSummary':{'totalCost':31456.74},'baseEstimate':27942.71,'contingency':3514.03}})
    assert r['total_cost']==31456.74 and r['p80'] is None
    assert r['contingency_pct']==pytest.approx(3514.03/27942.71*100)
