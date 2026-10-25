"""Execute the shipped UI renderers, including null costs and source text escaping."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_total_cost_charts_and_nine_agent_ledger_render_without_a_browser():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    page = Path("hackit2/liveview.html").read_text()
    script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    script = script.rsplit("if(typeof document!=='undefined')", 1)[0]
    checks = r"""
const assert = require('node:assert/strict');
const data={our_costs:[125000,null,121000],their_costs:[135000,129000,124000],
sweet_spot_cost:120000,fallback_cost:140000};
const plot=chart(data);
assert(plot.includes('Total buyer cost C'));
assert(!plot.includes('NaN')&&!plot.includes('Infinity'));
assert(chart({our_prices:[10],their_prices:[12]}).includes('total cost was not captured'));
const cost={total_cost:120000,breakdown:{price:100000,payment_terms:-500,relationship:20500},
relationship_estimate_available:true};
assert(costBreakdownHTML(cost).includes('-500.00'));
assert(sheetChip({unit_price:10},0,1,cost).includes('Total C €120000'));
assert(sheetChip({unit_price:10},0,1,125000).includes('Total C €125000'));
assert(costBreakdownHTML(125000).includes('breakdown was not recorded'));
assert(!chart(data).includes('Each point uses its complete package'));
assert(chart({...data,cost_basis:'exact historical packages'}).includes('Each point uses its complete package'));
const evidence=[{claim:'<script>bad</script>',source_ids:['source:1'],status:'inferred',confidence:'low'}];
const ledger=researchLedgerHTML({research:{version:'alpha-nine-v1',
reports:{scout_discovery:{evidence}},sources:{},tool_calls:[]}});
assert(ledger.includes('&lt;script&gt;')&&!ledger.includes('<script>'));
S=fresh(); S.prep.stages.specialists={agents:9};
assert(prepHTML().includes('8 experts + Scout discovery'));
assert.equal(roleNode('researcher:scout_discovery'),'research.scout');
assert(mapSVG().includes('Scout researcher'));
S.suppliers=['sup:x']; S.end={deal:true};
assert.equal(researchApplicationStatus(),'0/1 · fallback');
S.events.push({event:'brief',supplier:'sup:x'});
assert.equal(researchApplicationStatus(),'1/1 strategies applied');
console.log('UI renderer checks passed');
"""
    driver = (
        "const vm=require('node:vm');vm.runInNewContext("
        + json.dumps(script + checks)
        + ", {require,console,performance:require('node:perf_hooks').performance});"
    )
    result = subprocess.run([node, "-e", driver], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "UI renderer checks passed" in result.stdout
