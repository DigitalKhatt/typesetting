from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import patch

from fontTools.varLib.builder import buildVarDevTable

from fonts.portable.development.project import FAMILIES
from fonts.portable.development.validation import release_attachments, release_checks, release_rendering
from fonts.portable.development.validation.native_capture import approve, renderer_source
from fonts.portable.font_checks import file_hash
from fonts.portable.development.validation.release_rendering import assess
from fonts.portable.development.validation.release_weights import adaptive_sweep, coverage_plan, F2DOT14
from tools.font_validation.validate import save


class ReleaseChecksTest(unittest.TestCase):
    def test_only_live_regions_and_adjacent_switch_ticks_are_required(self):
        def region(peak):
            return Obj(VarRegionAxis=[Obj(StartCoord=0, PeakCoord=peak, EndCoord=1)])
        store = Obj(VarData=[Obj(VarRegionIndex=[0,1], Item=[[2,0]])],
                    VarRegionList=Obj(Region=[region(.375),region(.625)]))
        condition = Obj(Format=1, AxisIndex=0, FilterRangeMinValue=1/F2DOT14, FilterRangeMaxValue=1)
        fv = Obj(FeatureVariationRecord=[Obj(ConditionSet=Obj(ConditionTable=[condition]))])
        font = {'fvar': Obj(axes=[Obj(axisTag='wght',minValue=400,defaultValue=400,maxValue=700)]),
                'GPOS': Obj(table=Obj(pos=[Obj(device=buildVarDevTable(0))])),
                'GSUB': Obj(table=Obj(FeatureVariations=fv)), 'GDEF': Obj(table=Obj(VarStore=store))}
        plan = coverage_plan(font)
        self.assertIn('GPOS variation region',plan[512.5])
        self.assertNotIn(587.5,plan)
        for tick in (0,1,2):
            self.assertIn('GSUB feature switch',plan[400+300*tick/F2DOT14])
        font['avar'] = Obj(majorVersion=1,segments={'wght':{0:0,.5:.375,1:1}})
        self.assertIn('GPOS variation region',coverage_plan(font)[550])

    def test_refinement_follows_nearby_intervals(self):
        def measure(batch):
            return [dict(weight=w,reviewPairs=int(w == 400)) for w in batch]
        rows = adaptive_sweep([400,500,600],measure,2)
        self.assertEqual([r['weight'] for r in rows],[400,425,450,500,600])

    def test_independent_switches_and_order_after_failure(self):
        with TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
            root = Path(tmp)
            name = 'madina'
            family = root/'fonts'/name
            family.mkdir(parents=True)
            font = family/(FAMILIES[name]+'-Portable-VF.ttf')
            font.write_bytes(b'fixture')
            save(family/'report.json', dict(family=name,fontSha256=file_hash(font)))
            args = ['--fonts',str(root/'fonts'),'--font',name]
            observed = []
            def record(label, status):
                def run(*args):
                    observed.append(label)
                    return dict(status=status)
                return run
            with (patch.object(release_checks, 'weight_check', side_effect=record('weights', 'failed')),
                  patch.object(release_attachments, 'check_attachments', side_effect=record('links', 'passed')),
                  patch.object(release_rendering, 'check_rendering', side_effect=record('apps', 'needs-review'))):
                self.assertEqual(release_checks.main(args),1)
                self.assertEqual(observed,['weights','links','apps'])
                observed.clear()
                self.assertEqual(release_checks.main(args+['--no-attachments']),1)
                self.assertEqual(observed,['weights','apps'])
                observed.clear()
                self.assertEqual(release_checks.main(args+['--no-weight-coverage','--no-app-rendering']),0)
                self.assertEqual(observed,['links'])
                self.assertEqual(json.loads((root/'release/release.json').read_text())['status'],'passed-selected')
                self.assertEqual(release_checks.main(args+['--no-weight-coverage','--no-attachments','--no-app-rendering']),0)
                self.assertEqual(json.loads((root/'release/release.json').read_text())['status'],'disabled')

    def test_ownership_stack_join_and_kern_have_independent_controls(self):
        names = ['a.init','below','above','b.fina']
        class Font(dict):
            def getGlyphOrder(self):
                return names
            def normalizeLocation(self, value):
                return {'wght':1}
        class Ink:
            def __init__(self, *args):
                pass
            def path(self, gid):
                return Obj(bounds=(0,0,5,5),area=25)
            def meets(self, a,b,dx,dy):
                return dx == -10 and dy == 0
        native = Font(GDEF=Obj(table=Obj(GlyphClassDef=Obj(classDefs={'below':3,'above':3}))))
        records = [(0,0,10,0,0,-1,3,0),(1,1,0,0,-12,0,-1,-1),
                   (2,2,0,0,12,1,-1,-1),(3,3,10,-10,0,-1,-1,-1)]
        def write(path, rows):
            path.write_text('|'.join(','.join(map(str,r)) for r in rows)+'\n')
            return path
        cursive = {0: [{0: (None, (0, 0)), 3: ((10, 0), None)}]}
        with (TemporaryDirectory() as tmp, patch.object(release_attachments, 'Ink', Ink),
              patch.object(release_attachments, 'cursive_records', return_value=cursive)):
            root = Path(tmp)
            reference = write(root/'native',records)
            candidate = [*records]
            candidate[0] = (0,0,13,0,0,-1,3,0)
            def measure(rules):
                return release_attachments.check_weight(native,native,reference,write(root/'final',candidate),reference,
                    [dict(page=1,line=1)],700,rules,Ink())['findings']
            kern = [[['a.init'], 0, 'kern', [0], [3]]]
            self.assertEqual(measure(kern), {})
            candidate[0] = (0, 0, 13, 0, 2, -1, 3, 0)
            vertical_kern = [[['a.init'], 0, 'kern', [0, 0], [3, 2]]]
            self.assertEqual(set(measure(vertical_kern)), {'anchor-alignment', 'disconnected-stroke'})
            candidate[0] = records[0]
            candidate[2] = (2,2,0,0,-24,0,-1,-1)
            candidate[3] = (3,3,10,-16,0,-1,-1,-1)
            self.assertEqual(set(measure(kern)), {'mark-parent','stack-order','anchor-alignment','disconnected-stroke',
                                                  'contextual-kern','word-advance'})

    def test_attachments_sample_intermediate_kern_weights(self):
        rules = [
            [['a.init', 'b.fina'], 1, 'kern', [0], [0], [[410, [4]], [485, [0]]]],
            [['a.init', 'mark'], 1, 'mark', [0, 0], [0, 0], [[460, [0, 2]]]],
        ]
        plan = {400: {'axis/grid'}, 425: {'axis/grid'}, 400.02: {'GSUB feature switch'}}
        with patch.object(release_attachments, 'coverage_plan', return_value=plan):
            self.assertEqual(release_attachments.attachment_weights(None, rules),
                             [400, 400.02, 410, 485, 550, 700])

    def test_native_images_require_approval_and_expose_closed_counter(self):
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self.skipTest('Install the optional release image dependencies')
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit, cap, gold = root/'kit', root/'captures/android/madina/runs/a',root/'goldens'
            cases = [dict(id=f's{i}',sample='s',scale=i,width=8, height=8,guard=1) for i in (1,4)]
            save(kit/'cases.json',dict(family='madina',fontSha256='new',samplesSha256='text',cases=cases))
            cap.mkdir(parents=True)
            save(cap.parents[1]/'latest.json',{'run':'runs/a'})
            manifest = dict(platform='android',family='madina',kitSha256=file_hash(kit/'cases.json'),
                            samplesSha256='text',fontSha256='new',renderer='native',os='test',cases=[],
                            rendererSourceSha256=renderer_source('android'))
            for case in cases:
                img = Image.new('RGB',(10,10),'white')
                ImageDraw.Draw(img).rectangle((2,2,7,7),outline='black')
                png = cap/(case['id']+'.png')
                img.save(png)
                manifest['cases'].append(dict(id=case['id'],glyphs=1,missingGlyphs=0,fallbackGlyphs=0,
                                              pngSha256=file_hash(png)))
            save(cap/'capture.json',manifest)
            def result():
                return assess('android',kit,root/'captures',gold,root/'diff')
            self.assertEqual(result()['status'],'needs-review')
            approve(cap,gold)
            self.assertEqual(result()['status'],'passed')
            ImageDraw.Draw(img).rectangle((2,2,7,7), fill='black')
            img.putpixel((0,0),(0,0,0))
            img.save(cap/'s1.png')
            manifest['cases'][0].update(pngSha256=file_hash(cap/'s1.png'),fallbackGlyphs=1)
            save(cap/'capture.json',manifest)
            self.assertEqual(set(result()['cases'][0]['issues']),
                             {'clipped ink','fallback','pixels changed','fewer open counters than approved'})
            manifest['rendererSourceSha256'] = 'older renderer'
            save(cap/'capture.json',manifest)
            self.assertEqual(result()['status'],'needs-review')
