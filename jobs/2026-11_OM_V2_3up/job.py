"""Validate supplied 3-up nesting; prepare CAM geometry, never send or emit RML.

python jobs/2026-11_OM_V2_3up/job.py [--exact]
--exact adds slow BRep distance/intersection/containment checks.
Coordinates: A (as supplied, reliefs up) X=x+196.5,Y=y+160,Z=z-10.
B after flipping the entire blank about X: X=x+196.5,Y=160-y,Z=-z.
"""
from pathlib import Path
import sys, json, csv, math, hashlib, itertools, argparse
import cadquery as cq
import numpy as np
import shapely
from shapely.geometry import Polygon, box, Point, LineString
from shapely.ops import unary_union
from shapely import affinity
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = ROOT/'model/OM_V2_Arrangement.step'
STOCK = box(0, 0, 393, 320)
DEFL = .002
MARGIN = .02

def polys(g):
    return [g] if g.geom_type == 'Polygon' else [p for p in getattr(g,'geoms',[]) if p.geom_type=='Polygon']

def lines(g):
    return [g] if g.geom_type in ('LineString','LinearRing') else [p for p in getattr(g,'geoms',[]) if p.geom_type in ('LineString','LinearRing')]

def section(s,z):
    wires = cq.Workplane('XY',origin=(0,0,z)).add(s).section().val().Wires()
    result = Polygon()
    for w in wires:
        points,_ = w.sample(DEFL)
        p = Polygon([(v.x+196.5,v.y+160) for v in points]).buffer(0)
        result = result.symmetric_difference(p)
    return result

def filled(g):
    return unary_union([Polygon(p.exterior) for p in polys(g)])

def trace(g,r):
    return unary_union([LineString(p.exterior.coords) for p in polys(g.buffer(r+MARGIN,quad_segs=64))])

def plot(g,ax,color,fill=False,**kwargs):
    for p in polys(g):
        if fill:
            ax.fill(*p.exterior.xy,color=color,**kwargs)
            for hole in p.interiors: ax.fill(*hole.xy,color='white')
        else: ax.plot(*p.exterior.xy,color=color,**kwargs)

def prepare_tables(report):
    """Update review tables without repeating expensive BRep validation."""
    with (HERE/'holes_original.csv').open(encoding='utf-8-sig') as src:
        original=list(csv.DictReader(src))
    poses=[]
    for i in range(1,4):
        group=[r for r in original if int(r['part'])==i]
        assert len(group)==23
        center=next(r for r in group if abs(float(r['diameter_mm'])-12.2)<1e-5)
        bolt=next(r for r in group if abs(float(r['diameter_mm'])-6.2)<1e-5)
        angle=math.degrees(math.atan2(float(bolt['A_Y'])-float(center['A_Y']),float(bolt['A_X'])-float(center['A_X'])))
        if angle>90: angle-=180
        if angle<=-90: angle+=180
        poses.append({'part':i,'original_center_A':[float(center['A_X']),float(center['A_Y'])],
                      'rotation_A_degrees_mod180':round(angle,5),
                      'candidate_center_A':[float(center['A_X']),float(center['A_Y'])+(2.5 if i==2 else 0)]})
    report['part_poses']=poses
    for variant in ('original','candidate_P2_Yplus2p5'):
        holes=[]
        for row in original:
            item=dict(row)
            if variant!='original' and int(item['part'])==2:
                item['A_Y']=round(float(item['A_Y'])+2.5,5)
                item['B_Y']=round(float(item['B_Y'])-2.5,5)
            holes.append(item)
        if variant!='original':
            with (HERE/f'holes_{variant}.csv').open('w',encoding='utf-8-sig',newline='') as out:
                wr=csv.DictWriter(out,fieldnames=list(original[0])); wr.writeheader(); wr.writerows(holes)
        with (HERE/f'fixture_{variant}.csv').open('w',encoding='utf-8-sig',newline='') as out:
            wr=csv.writer(out); wr.writerow(['clamp','A_X','A_Y','B_X','B_Y','pad_width_mm','pad_depth_mm','status'])
            for i,pad in enumerate(report[variant]['clamp_pads'],1):
                wr.writerow([i,*pad['A_center'],*pad['B_center'],*pad['size'],'CONTACT_PAD_CANDIDATE_ACTUAL_HARDWARE_UNMODELLED'])
        tooltimes=[]
        for diameter,depth,ap,feed in ((6.,10.4,.2,180),(2.,11.,.1,150),(1.,10.4,.03,80)):
            loop_sum=sum(t['nominal_centerline_length_mm'] for t in report[variant]['tools'][str(diameter)])
            tooltimes.append({'D_mm':diameter,'loop_count':math.ceil(depth/ap-1e-9),
                              'three_part_outline_feed_minutes_only':loop_sum*math.ceil(depth/ap-1e-9)/feed,
                              'note':'Screening estimate only, not generated 3D paths; excludes holes, R surfaces, rest cuts, tabs, air moves and tool changes'})
        report[variant]['outline_tool_screening']=tooltimes
    report['selected_layout']='original (user confirmed)'
    report['selected_retention']='external frame clamps and tabs (user confirmed); part bolting rejected'
    report['fixture_plan']={'sequence':'A: reliefs with protected frame; flip complete blank; B: holes, limited D6/D2 rough to depth9, D1 final narrow cut with tabs',
                            'hardware':'External clamp contact pads proposed; actual clamp and holder geometry still required.',
                            'scrap_retention':'Keep bottom1mm frame web during coarse roughing. Final D1 channel with four tabs/part. Verify all scrap and parts stay linked to clamp pads.',
                            'tabs':'4 per part, width2.5 thickness0.4 proposed; locations and connectivity not yet validated',
                            'registration':'Two external datum references needed; old 2-up datum proposal is invalid for 3-up'}
    report['cutting_conditions']='Existing approved three-tool provisional conditions retained; no A6063 cutting trial recorded'
    report['input_hashes']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [SOURCE,ROOT/'model/393_320_10.step',ROOT/'model/OM_BASE_NC.step',ROOT/'ツール在庫.xlsx']}
    (HERE/'preparation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

def retention(report):
    """Voxel connectivity of a specified retained-web plan, not cutter simulation."""
    from scipy import ndimage
    parts=[s for s in cq.importers.importStep(str(SOURCE)).solids().vals() if len(s.Faces())>6]
    outer=[filled(section(s,.01)) for s in parts]
    allparts=unary_union(outer)
    tabs=[]; tabshapes=[]
    for pose,g,s in zip(report['part_poses'],outer,parts):
        cx,cy=pose['original_center_A']; phi=pose['rotation_A_degrees_mod180']
        lower=section(s,9.8)
        for k,angle in enumerate((0,72,180,252),1):
            a=math.radians(angle+phi); nx,ny=math.cos(a),math.sin(a)
            ray=LineString([(cx,cy),(cx+150*nx,cy+150*ny)])
            hits=g.boundary.intersection(ray)
            pts=[hits] if hits.geom_type=='Point' else [p for p in getattr(hits,'geoms',[]) if p.geom_type=='Point']
            assert pts
            p=max(pts,key=lambda p:p.distance(Point(cx,cy))); tx,ty=-ny,nx
            # Full depth tab root must be on solid material, away from A reliefs.
            assert lower.covers(Point(p.x-nx,p.y-ny)), 'Tab overlaps underside relief'
            bridge=Polygon([(p.x+u*nx+v*tx,p.y+u*ny+v*ty) for u,v in [(-1.5,-1.25),(7.5,-1.25),(7.5,1.25),(-1.5,1.25)]])
            assert bridge.intersection(unary_union([q for q in outer if q is not g])).area<1e-6, 'Tab enters another part'
            tabshapes.append(bridge)
            tabs.append({'part':pose['part'],'tab':k,'A_X':p.x,'A_Y':p.y,'B_X':p.x,'B_Y':320-p.y,'width':2.5,'thickness':.4,'B_top_Z':-9.6})
    # D6 roughing is clipped against neighboring part/tool clearance; no material plunge.
    coarse=[]
    for i,g in enumerate(outer):
        other=unary_union([q for j,q in enumerate(outer) if j!=i])
        path=trace(g,3).difference(other.buffer(3+MARGIN+2*DEFL))
        coarse.append(path.buffer(3))
    # D2 rest rough may occupy a full D2 outline band, but stops at depth9.
    rough=unary_union(coarse+[trace(g,1).buffer(1) for g in outer])
    final=unary_union([trace(g,.5).buffer(.5) for g in outer])
    tab_union=unary_union(tabshapes)
    res=.5; dz=.2
    xs=np.arange(res/2,393,res); ys=np.arange(res/2,320,res)
    xx,yy=np.meshgrid(xs,ys)
    roughmask=shapely.contains_xy(rough,xx,yy)
    finalmask=shapely.contains_xy(final,xx,yy)
    tabmask=shapely.contains_xy(tab_union,xx,yy)
    # Outside the final part footprint, maintain an uncut 1mm frame web throughout A.
    # This is an explicit CAM boundary requirement, not inferred from unspecified paths.
    outside=~shapely.contains_xy(allparts,xx,yy)
    layers=[]
    for z in np.arange(dz/2,10,dz):  # original Z is B cutting depth
        solid=np.zeros(xx.shape,dtype=bool)
        for s in parts: solid |= shapely.contains_xy(section(s,float(z)),xx,yy)
        scrap=outside.copy()
        cut=finalmask.copy()
        if z<9: cut |= roughmask
        if z>9.6: cut &= ~tabmask
        material=(solid|scrap)&~cut
        if z>9.6: material |= tabmask
        layers.append(material)
    vox=np.stack(layers)
    labels,n=ndimage.label(vox)
    counts=np.bincount(labels.ravel()); counts[0]=0
    significant=np.where(counts*res*res*dz>=1.)[0]
    clamp_labels=set()
    for pad in report['original']['clamp_pads']:
        x,y=pad['A_center']; ix=int(x/res); iy=int(y/res)
        clamp_labels.update(int(v) for v in labels[:,iy,ix] if v)
    unanchored=[{'label':int(i),'volume_mm3':float(counts[i]*res*res*dz)} for i in significant if i not in clamp_labels]
    partlabels=[]
    for pose in report['part_poses']:
        cx,cy=pose['original_center_A']
        # Center is a through hole. Sample eight mm off center, inside the main disk.
        partlabels.append(int(labels[1,int(cy/res),int((cx+8)/res)]))
    check={'resolution_XY_mm':res,'resolution_Z_mm':dz,'significant_components':len(significant),
           'clamp_connected_labels':sorted(clamp_labels),'part_labels':partlabels,'unanchored_components':unanchored,
           'geometric_connectivity_pass':not unanchored and all(p in clamp_labels for p in partlabels),
           'requirements':['A: keep stock outside the final projection intact at original Z9..10 (B bottom web); apply machining boundaries and tab keepouts',
                           'B: all D6 and D2 external rough stops at depth9; D1 final removes only narrow channel',
                           'B: all final passes preserve tab regions below Z-9.6; no later D6 cut through the frame',
                           'Clamp actual hardware on proposed pads and model holders before NC output'],
           'limits':'Voxel topology only. Does not verify cutting forces, web stiffness, exact shank/holder clearance or tool entry.'}
    report['retention_validation']=check
    with (HERE/'tabs_original.csv').open('w',encoding='utf-8-sig',newline='') as out:
        wr=csv.DictWriter(out,fieldnames=list(tabs[0])); wr.writeheader(); wr.writerows(tabs)
    asm=cq.Assembly(name='TAB_REGIONS_REFERENCE_NOT_FINISHED_PARTS')
    for i,g in enumerate(tabshapes,1):
        g=affinity.scale(g,xfact=1,yfact=-1,origin=(0,160))
        wire=cq.Workplane('XY').polyline(list(g.exterior.coords)[:-1]).close().extrude(.4).val().translate((0,0,-10))
        asm.add(wire,name=f'tab_{i}')
    asm.export(str(HERE/'tab_keepouts_B_bottom_reference.step'))
    fig,ax=plt.subplots(figsize=(11,9)); ax.add_patch(Rectangle((0,0),393,320,facecolor='#eee',edgecolor='black'))
    plot(rough,ax,'#edb6b6',True,alpha=.6)
    for g in outer: plot(g,ax,'#9ec8e3',True)
    for tab,g in zip(tabs,tabshapes):
        plot(g,ax,'#e99c19',True)
        ax.text(tab['A_X'],tab['A_Y'],f"{tab['part']}-{tab['tab']}",fontsize=7)
    for pad in report['original']['clamp_pads']:
        x,y=pad['A_center']; ax.add_patch(Rectangle((x-6,y-6),12,12,fill=False,edgecolor='green'))
    ax.set_aspect('equal'); ax.set(xlim=(-5,398),ylim=(-5,325),title='Original layout: tab bridges (orange); D6/D2 rough stays above bottom1mm web',xlabel='A X / mm',ylabel='A Y / mm')
    fig.savefig(HERE/'retention_original.png',dpi=150); plt.close(fig)
    (HERE/'preparation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(check,indent=2),flush=True)

def frame_keepouts():
    """Exact outside-frame top 1mm protection for A; reflected bottom1mm for B."""
    solids=cq.importers.importStep(str(SOURCE)).solids().vals()
    parts=[s for s in solids if len(s.Faces())>6]
    protected=cq.Workplane('XY').box(393,320,1,centered=(False,False,False)).val().translate((-196.5,-160,9))
    for s in parts:
        wires=cq.Workplane('XY',origin=(0,0,.01)).add(s).section().val().Wires()
        outer=max(wires,key=lambda w:w.BoundingBox().xlen*w.BoundingBox().ylen)
        face=cq.Face.makeFromWires(outer)
        cutter=cq.Workplane('XY').add(face).extrude(1.2).val().translate((0,0,8.89))
        protected=protected.cut(cutter)
    a=protected.translate((196.5,160,-10))
    b=a.rotate((0,160,-5),(1,160,-5),180)
    assert a.isValid() and b.isValid()
    cq.exporters.export(a,str(HERE/'protected_frame_A_reference.step'))
    cq.exporters.export(b,str(HERE/'protected_frame_B_reference.step'))

def analyze(parts, name, exact=False):
    sections = [[section(s,z) for z in (.01,1.5,3.99,4.01,5.5,7.,9.99)] for s in parts]
    shapes = [unary_union(ss).buffer(0) for ss in sections]
    outer = [filled(g) for g in shapes]
    pairs = []
    for i,j in itertools.combinations(range(3),2):
        item = {'parts':[i+1,j+1], 'projected_gap_mm':outer[i].distance(outer[j]),
                'projected_overlap_mm2':outer[i].intersection(outer[j]).area}
        if exact:
            item.update(exact_solid_gap_mm=parts[i].distance(parts[j]),
                        exact_overlap_mm3=parts[i].intersect(parts[j]).Volume())
        pairs.append(item)
    result = {'pair_clearance':pairs, 'parts':[], 'tools':{}, 'clamp_pads':[],
              'polyline_deflection_mm':DEFL,'clearance_margin_mm':MARGIN}
    stocksolid = cq.Workplane('XY').box(393,320,10,centered=(False,False,False)).val().translate((-196.5,-160,0))
    for i,s in enumerate(parts):
        b=s.BoundingBox()
        item={'id':i+1, 'bounds_A':[b.xmin+196.5,b.ymin+160,-10+b.zmin,b.xmax+196.5,b.ymax+160,-10+b.zmax],
              'edge_margins_mm':{'left':b.xmin+196.5,'right':196.5-b.xmax,'front':b.ymin+160,'back':160-b.ymax},
              'area_A_top':sections[i][-1].area,'area_B_top':sections[i][0].area,
              'B_roof_over_A_relief_mm2':sections[i][0].difference(sections[i][-1]).area}
        if exact: item['outside_stock_mm3']=s.cut(stocksolid).Volume()
        result['parts'].append(item)
    sweeps={}; routes={}
    for d in (6.,2.,1.):
        nominal=[trace(g,d/2) for g in outer]
        sweeps[d]=[g.buffer(d/2,quad_segs=64) for g in nominal]
        routes[d]=[]; items=[]
        for i,ring in enumerate(nominal):
            others=unary_union([g for j,g in enumerate(outer) if j!=i])
            forbidden=others.buffer(d/2+MARGIN+DEFL*2,quad_segs=64)
            safe=ring.difference(forbidden)
            routes[d].append(safe)
            items.append({'part':i+1,'nominal_centerline_length_mm':ring.length,
                          'neighbor_gouge_area_mm2':sweeps[d][i].intersection(others).area,
                          'unsafe_centerline_length_mm':ring.intersection(forbidden).length,
                          'safe_fragments':len(lines(safe)),
                          'swept_bounds':list(sweeps[d][i].bounds),
                          'sweep_outside_stock_mm2':sweeps[d][i].difference(STOCK).area})
        result['tools'][str(d)]=items
    # Candidate clamp contact pads: 12x12 mm, with >=8mm gap from complete D6 sweep.
    # These are geometric candidates; actual clamp hardware/fasteners are not modelled.
    busy=unary_union(sweeps[6.]).buffer(8)
    candidates=[]
    for x,y in itertools.product(range(10,384,10),range(10,311,10)):
        pad=box(x-6,y-6,x+6,y+6)
        if STOCK.covers(pad) and pad.disjoint(busy):
            candidates.append((min(x,393-x,y,320-y),x,y,pad))
    candidates.sort()
    selected=[]
    for _,x,y,pad in candidates:
        if all(math.dist((x,y),(a,b))>=65 for a,b in selected):
            selected.append((x,y)); result['clamp_pads'].append({'A_center':[x,y],'B_center':[x,320-y],
                                   'size':[12,12], 'D6_sweep_clearance_mm':pad.distance(unary_union(sweeps[6.]))})
        if len(selected)>=8: break
    # Scrap left after full D6 slots, ignoring tabs: fragmentation flags retention risk.
    frame=STOCK.difference(unary_union([g.buffer(6+MARGIN,quad_segs=64) for g in outer]))
    result['scrap_components_after_full_D6_slots_mm2']=sorted([p.area for p in polys(frame) if p.area>1],reverse=True)
    result['registration_candidates_A']=[p['A_center'] for p in result['clamp_pads']]
    result['simulation_status']='Geometry only; no depth toolpath, tabs, shank or holder simulation'
    (HERE/f'{name}_analysis.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    fig,ax=plt.subplots(figsize=(12,10)); ax.add_patch(Rectangle((0,0),393,320,facecolor='#eee',edgecolor='black'))
    for i,g in enumerate(shapes):
        plot(g,ax,['#80b8e0','#9ed1ad','#e9bd86'][i],True)
        center=outer[i].centroid; ax.text(center.x,center.y,f'P{i+1}',ha='center')
        for line in lines(routes[6.][i]): ax.plot(*line.xy,color='#cc3333',linewidth=.7)
        bad=trace(outer[i],3).difference(routes[6.][i])
        for line in lines(bad): ax.plot(*line.xy,color='magenta',linewidth=3)
    for c in result['clamp_pads']:
        x,y=c['A_center']; ax.add_patch(Rectangle((x-6,y-6),12,12,fill=False,edgecolor='green',linewidth=2))
    ax.set(xlim=(-12,405),ylim=(-12,332),xlabel='A X / mm',ylabel='A Y / mm',title=f'{name}: red=D6 centerline; magenta=neighbor collision; green=contact pad candidates')
    ax.set_aspect('equal'); ax.grid(alpha=.2); fig.savefig(HERE/f'{name}.png',dpi=150); plt.close(fig)
    # Review geometry, NOT executable moves: no Z/feed/entry/tab definitions.
    with (HERE/f'{name}_contours.csv').open('w',encoding='utf-8-sig',newline='') as out:
        writer=csv.writer(out); writer.writerow(['part','D_mm','fragment','point','A_X_mm','A_Y_mm','B_X_mm','B_Y_mm','status'])
        for d in (6.,1.):
            for i,g in enumerate(routes[d]):
                for k,line in enumerate(lines(g),1):
                    for n,(x,y) in enumerate(line.coords):
                        writer.writerow([i+1,d,k,n,round(x,5),round(y,5),round(x,5),round(320-y,5),'REVIEW_ONLY_NO_DEPTH_NO_TABS'])
    return result,shapes

def main():
    args=argparse.ArgumentParser(); args.add_argument('--exact',action='store_true'); args.add_argument('--tables-only',action='store_true'); args.add_argument('--retention',action='store_true'); args.add_argument('--frame-keepouts',action='store_true'); opts=args.parse_args()
    if opts.tables_only:
        report=json.loads((HERE/'preparation.json').read_text(encoding='utf-8'))
        assert report['source_sha256']==hashlib.sha256(SOURCE.read_bytes()).hexdigest(), 'Source changed: run full analysis'
        prepare_tables(report)
        if opts.retention: retention(report)
        if opts.frame_keepouts: frame_keepouts()
        return
    solids=cq.importers.importStep(str(SOURCE)).solids().vals()
    stock=[s for s in solids if len(s.Faces())==6 and abs(s.Volume()-1257600)<.01]
    parts=[s for s in solids if s not in stock]
    assert len(stock)==1 and len(parts)==3 and all(s.isValid() for s in solids)
    b=stock[0].BoundingBox()
    assert abs(b.xmin+196.5)<1e-5 and abs(b.ymin+160)<1e-5 and abs(b.zmin)<1e-5
    assert abs(b.xlen-393)<1e-5 and abs(b.ylen-320)<1e-5 and abs(b.zlen-10)<1e-5
    report={'source':str(SOURCE.relative_to(ROOT)),'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            'stock_mm':[393,320,10], 'material':'A6063','tool_selection':'previously approved D6 flat + PRB2.0-21 ball D2 + AC40-TLNE2010-14 square D1',
            'A_map':'X=x+196.5;Y=y+160;Z=z-10 (reliefs up, as supplied)',
            'B_map':'X=x+196.5;Y=160-y;Z=-z',
            'flip':'(X,Y,Z)->(X,320-Y,-10-Z)',
            'status':'CONDITIONAL_PREPARATION_NO_EXECUTABLE_PATH',
            'original':None,'candidate_P2_Yplus2p5':None}
    report['original'],shapes=analyze(parts,'original',opts.exact)
    print('Original analyzed',flush=True)
    moved=[parts[0],parts[1].translate((0,2.5,0)),parts[2]]
    report['candidate_P2_Yplus2p5'],_=analyze(moved,'candidate_P2_Yplus2p5',False)
    print('Candidate analyzed',flush=True)
    holes=[]
    for i,s in enumerate(parts,1):
        wires=cq.Workplane('XY',origin=(0,0,1)).add(s).section().val().Wires()
        for w in wires:
            es=w.Edges()
            if len(es)==1 and es[0].geomType()=='CIRCLE':
                center=w.Center(); bb=w.BoundingBox()
                holes.append([i,round((bb.xlen+bb.ylen)/2,5),round(center.x+196.5,5),round(center.y+160,5),round(center.x+196.5,5),round(160-center.y,5)])
    assert len(holes)==69
    with (HERE/'holes_original.csv').open('w',encoding='utf-8-sig',newline='') as out:
        writer=csv.writer(out); writer.writerow(['part','diameter_mm','A_X','A_Y','B_X','B_Y']); writer.writerows(holes)
    report['holes_count']=len(holes)
    # Two distinct files ensure the proposed change cannot silently replace user geometry.
    for label,group in [('original',parts),('candidate_P2_Yplus2p5',moved)]:
        asm=cq.Assembly(name=label+'_CAM_REFERENCE_ONLY')
        asm.add(stock[0].translate((196.5,160,-10)),name='STOCK_NOT_MODEL',color=cq.Color(.7,.7,.7,.25))
        for i,s in enumerate(group,1): asm.add(s.translate((196.5,160,-10)),name=f'P{i}',color=cq.Color(.2,.6,.9))
        asm.export(str(HERE/f'{label}_A_reference.step'))
    (HERE/'preparation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    prepare_tables(report)
    if opts.retention: retention(report)
    if opts.frame_keepouts: frame_keepouts()
    print(json.dumps({label:{'pairs':report[label]['pair_clearance'],'D6':report[label]['tools']['6.0'],'clamp_pads':report[label]['clamp_pads']} for label in ['original','candidate_P2_Yplus2p5']},indent=2))

if __name__=='__main__': main()
