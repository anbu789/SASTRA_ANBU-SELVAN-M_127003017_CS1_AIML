import json, copy
from pathlib import Path
INPUT = Path(__file__).resolve().parent.parent / "Input_Data"
HLD_DIR = INPUT / "hld"
for d in ("hld", "specs", "ground_truth"): (INPUT/d).mkdir(parents=True, exist_ok=True)

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

SR, CS = "Sender-Receiver", "Client-Server"
def P(port, d, itf, typ, el, dtype, unit):
    return dict(port=port, dir=d, interface=itf, itype=typ, element=el, dtype=dtype, unit=unit)

SPEC_V10 = dict(
  title="Cruise Control Subsystem - AUTOSAR High-Level Design", version="1.0",
  revisions=[["1.0","2026-09-01","Initial release"]],
  components=[
    dict(name="SWC_WheelSpeedSensor", kind="Sensor-actuator SWC", runnable="Rn_ReadWheelSpeed", period_ms=10,
         desc="Reads the wheel speed pulses through the ECU abstraction layer and publishes the filtered wheel speed to the application layer.",
         ports=[P("Pp_WheelSpeed","P","If_WheelSpeed",SR,"wheelSpeed","uint16","km/h")]),
    dict(name="SWC_VehicleSpeedCalc", kind="Application SWC", runnable="Rn_CalcVehicleSpeed", period_ms=10,
         desc="Calculates the vehicle speed and the longitudinal acceleration from the wheel speed and publishes both values.",
         ports=[P("Rp_WheelSpeed","R","If_WheelSpeed",SR,"wheelSpeed","uint16","km/h"),
                P("Pp_VehicleSpeed","P","If_VehicleSpeed",SR,"vehicleSpeed","uint16","km/h"),
                P("Pp_VehicleAccel","P","If_VehicleAccel",SR,"vehicleAccel","sint16","m/s2")]),
    dict(name="SWC_CruiseControl", kind="Application SWC", runnable="Rn_CruiseControlStep", period_ms=20,
         desc="Holds the driver set speed. It consumes the vehicle speed from the Vehicle Speed Calculator (SWC_VehSpeedCalc) and the brake status, and requests throttle from the engine control.",
         ports=[P("Rp_VehicleSpeed","R","If_VehicleSpeed",SR,"vehicleSpeed","uint16","km/h"),
                P("Rp_BrakeStatus","R","If_BrakeStatus",SR,"brakeActive","boolean","-"),
                P("Pp_ThrottleRequest","P","If_ThrottleRequest",SR,"throttlePct","uint8","%")]),
    dict(name="SWC_EngineControl", kind="Application SWC", runnable="Rn_ApplyThrottle", period_ms=10,
         desc="Converts the throttle request into an actuator command for the electronic throttle.",
         ports=[P("Rp_ThrottleRequest","R","If_ThrottleRequest",CS,"throttlePct","uint8","%")]),
    dict(name="SWC_DisplayCluster", kind="Application SWC", runnable="Rn_UpdateDisplay", period_ms=100,
         desc="Shows the current vehicle speed to the driver on the instrument cluster.",
         ports=[P("Rp_VehicleSpeed","R","If_VehicleSpeed",SR,"vehicleSpeed","uint8","mph")]),
  ])

SPEC_V11 = copy.deepcopy(SPEC_V10)
SPEC_V11["version"] = "1.1"
SPEC_V11["revisions"].append(["1.1","2026-09-20","Added SWC_BrakeManager; periods of SWC_WheelSpeedSensor and SWC_CruiseControl changed"])
for c in SPEC_V11["components"]:
    if c["name"]=="SWC_WheelSpeedSensor": c["period_ms"]=5
    if c["name"]=="SWC_CruiseControl":    c["period_ms"]=10
SPEC_V11["components"].append(dict(name="SWC_BrakeManager", kind="Sensor-actuator SWC", runnable="Rn_ReadBrake", period_ms=10,
    desc="Reads the brake pedal switch and publishes the brake status.",
    ports=[P("Pp_BrakeStatus","P","If_BrakeStatus",SR,"brakeActive","boolean","-")]))

ISSUES_TRUTH = [
  dict(id="ISS-01", type="missing_provider",        where="SWC_CruiseControl.Rp_BrakeStatus"),
  dict(id="ISS-02", type="interface_type_mismatch", where="If_ThrottleRequest (Sender-Receiver vs Client-Server)"),
  dict(id="ISS-03", type="data_type_mismatch",      where="If_VehicleSpeed.vehicleSpeed (uint16 vs uint8 in SWC_DisplayCluster)"),
  dict(id="ISS-04", type="unit_mismatch",           where="If_VehicleSpeed.vehicleSpeed (km/h vs mph in SWC_DisplayCluster)"),
  dict(id="ISS-05", type="orphan_provider",         where="SWC_VehicleSpeedCalc.Pp_VehicleAccel"),
  dict(id="ISS-06", type="terminology_inconsistency", where="SWC_VehSpeedCalc vs SWC_VehicleSpeedCalc"),
]
REVISION_TRUTH = [
  dict(type="component_added", where="SWC_BrakeManager"),
  dict(type="period_changed",  where="SWC_WheelSpeedSensor", old=10, new=5),
  dict(type="period_changed",  where="SWC_CruiseControl",    old=20, new=10),
  dict(type="issue_resolved",  where="ISS-01 (Pp_BrakeStatus now provided)"),
]

ss = getSampleStyleSheet()
TT = ParagraphStyle("TT", parent=ss["Title"],    fontSize=20, leading=24)
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=16, leading=20)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=13, leading=16)
H3 = ParagraphStyle("H3", parent=ss["Heading3"], fontSize=12, leading=15)
BODY = ParagraphStyle("B", parent=ss["BodyText"], fontSize=10, leading=13, spaceAfter=6)

def grid(rows, widths):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.5,colors.grey),
                           ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                           ("FONTSIZE",(0,0),(-1,-1),8)]))
    return t

def build_pdf(spec, path):
    st = [Paragraph(spec["title"], TT), Paragraph(f"Version {spec['version']}", BODY), Spacer(1,8),
          Paragraph("1 Introduction", H1),
          Paragraph("This document describes the high-level design of the cruise control subsystem on the vehicle ECU. "
                    "Intended readers are system architects, software developers, integration engineers and test engineers.", BODY),
          Paragraph("2 Architecture overview", H1),
          Paragraph(f"The subsystem consists of {len(spec['components'])} software components in the application layer. "
                    "Components communicate through ports typed by port interfaces, connected by the RTE.", BODY),
          Paragraph("2.1 Component catalogue", H2),
          grid([["Component","Kind","Runnable","Period (ms)"]] +
               [[c["name"],c["kind"],c["runnable"],str(c["period_ms"])] for c in spec["components"]], [150,110,140,70]),
          PageBreak(), Paragraph("3 Software component descriptions", H1)]
    for i, c in enumerate(spec["components"], 1):
        flow = f"Runnable {c['runnable']} is triggered every {c['period_ms']} ms by a timing event. "
        for p in c["ports"]:
            flow += (f"It reads {p['element']} through {p['port']}. " if p["dir"]=="R"
                     else f"It writes {p['element']} through {p['port']}. ")
        st += [Paragraph(f"3.{i} {c['name']}", H2), Paragraph(c["desc"], BODY),
               Paragraph("Ports", H3),
               grid([["Port","Dir","Interface","Interface type","Data element","Data type","Unit"]] +
                    [[p["port"],p["dir"],p["interface"],p["itype"],p["element"],p["dtype"],p["unit"]] for p in c["ports"]],
                    [105,28,85,85,75,55,42]),
               Spacer(1,6), Paragraph("Functional flow", H3), Paragraph(flow, BODY)]
        if i % 2 == 0: st.append(PageBreak())
    st += [Paragraph("4 Revision history", H1), grid([["Version","Date","Change"]] + spec["revisions"], [60,80,350])]
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=40, rightMargin=40, topMargin=50, bottomMargin=50).build(st)

def entities_from_spec(spec):
    comps = [dict(name=c["name"], kind=c["kind"], runnable=c["runnable"], period_ms=c["period_ms"]) for c in spec["components"]]
    ports = [dict(component=c["name"], **p) for c in spec["components"] for p in c["ports"]]
    itfs  = sorted({(p["interface"], p["itype"]) for p in ports})
    return dict(components=comps, ports=ports, interfaces=[dict(name=n, type=t) for n,t in itfs])

for spec in (SPEC_V10, SPEC_V11):
    build_pdf(spec, HLD_DIR/f"HLD_CruiseControl_v{spec['version']}.pdf")
    json.dump(entities_from_spec(spec), open(INPUT/"ground_truth"/f"ground_truth_entities_v{spec['version']}.json","w"), indent=1)
json.dump(ISSUES_TRUTH,   open(INPUT/"ground_truth"/"ground_truth_issues.json","w"), indent=1)
json.dump(REVISION_TRUTH, open(INPUT/"ground_truth"/"ground_truth_revision_diff.json","w"), indent=1)
print(sorted(p.name for p in HLD_DIR.iterdir()))