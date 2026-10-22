"""Leitor de 3MF: fixture sintética no formato do Bambu Studio / OrcaSlicer."""

import io
import struct
import zipfile

from django.test import SimpleTestCase

from apps.files3d import readers

SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value="02.01.00.59"/>
  </header>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="printer_model_id" value="N2S"/>
    <metadata key="nozzle_diameters" value="0.4"/>
    <metadata key="prediction" value="20400"/>
    <metadata key="weight" value="160.35"/>
    <object identify_id="1" name="cupula.stl" skipped="false"/>
    <filament id="1" tray_info_idx="GFL99" type="PLA" color="#FFFFFF" used_m="53.21" used_g="160.35"/>
  </plate>
  <plate>
    <metadata key="index" value="2"/>
    <metadata key="prediction" value="12000"/>
    <metadata key="weight" value="110.00"/>
    <object identify_id="2" name="base.stl" skipped="false"/>
    <filament id="2" type="PLA" color="#D9C7A6" used_m="36.50" used_g="110.00"/>
  </plate>
</config>
"""

PROJECT_SETTINGS = '{"printer_model": "Bambu Lab A1", "layer_height": "0.2", "nozzle_diameter": ["0.4"]}'

MODEL = """<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    <object id="1" type="model">
      <mesh>
        <vertices>
          <vertex x="0" y="0" z="0"/>
          <vertex x="180" y="0" z="0"/>
          <vertex x="0" y="180" z="0"/>
          <vertex x="0" y="0" z="270"/>
        </vertices>
        <triangles><triangle v1="0" v2="1" v3="2"/></triangles>
      </mesh>
    </object>
  </resources>
  <build><item objectid="1"/></build>
</model>
"""

GCODE = """; HEADER_BLOCK_START
; BambuStudio 02.01.00.59
; model printing time: 5h 39m 59s; total estimated time: 5h 46m 12s
; total filament used [g] = 160.35
; HEADER_BLOCK_END
; layer_height = 0.2
; printer_model = Bambu Lab A1
G28
"""

PNG = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + b"\x00" * 13 + b"\x00\x00\x00\x00IEND\xaeB`\x82"


def make_3mf(with_slice_info=True, with_gcode=False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("3D/3dmodel.model", MODEL)
        if with_slice_info:
            z.writestr("Metadata/slice_info.config", SLICE_INFO)
        z.writestr("Metadata/project_settings.config", PROJECT_SETTINGS)
        z.writestr("Metadata/plate_1.png", PNG)
        if with_gcode:
            z.writestr("Metadata/plate_1.gcode", GCODE)
    return buf.getvalue()


class Sliced3mfTests(SimpleTestCase):
    def test_reads_grams_time_and_thumbnail(self):
        data = readers.read_sliced_3mf(make_3mf())
        self.assertEqual(data["grams_total"], 270.35)
        self.assertEqual(data["time_s"], 32400)
        self.assertEqual(data["time_min"], 540)
        self.assertEqual(len(data["plates"]), 2)
        self.assertEqual([f["extruder"] for f in data["filaments"]], [1, 2])
        self.assertEqual(data["filaments"][0]["grams"], 160.35)
        self.assertEqual(data["filaments"][1]["color"], "#D9C7A6")
        self.assertEqual(data["printer_model"], "Bambu Lab A1")
        self.assertEqual(data["layer_height"], 0.2)
        self.assertEqual(data["object_count"], 2)
        self.assertEqual(data["bbox_mm"], {"x": 180.0, "y": 180.0, "z": 270.0, "vertices": 4})
        self.assertTrue(data["thumbnail_png"].startswith(b"\x89PNG"))

    def test_falls_back_to_gcode_header(self):
        data = readers.read_sliced_3mf(make_3mf(with_slice_info=False, with_gcode=True))
        self.assertEqual(data["grams_total"], 160.35)
        self.assertEqual(data["time_s"], 5 * 3600 + 46 * 60 + 12)

    def test_project_file_without_slicing_raises(self):
        with self.assertRaises(readers.ReadError):
            readers.read_sliced_3mf(make_3mf(with_slice_info=False))
        bbox = readers.read_project_3mf(make_3mf(with_slice_info=False))["bbox_mm"]
        self.assertEqual(bbox["z"], 270.0)

    def test_duration_parser(self):
        self.assertEqual(readers._parse_duration("5h 40m 3s"), 20403)
        self.assertEqual(readers._parse_duration("1d 2h"), 93600)
        self.assertEqual(readers._parse_duration("20403"), 20403)


class StlTests(SimpleTestCase):
    def test_reads_cube_dimensions_and_volume(self):
        import trimesh

        cube = trimesh.creation.box(extents=(20, 30, 40))
        data = readers.read_stl(io.BytesIO(cube.export(file_type="stl")), density_g_cm3=1.24)
        self.assertEqual(data["bbox_mm"], {"x": 20.0, "y": 30.0, "z": 40.0})
        self.assertEqual(data["volume_cm3"], 24.0)
        self.assertEqual(data["weight_solid_g"], 29.8)
        self.assertTrue(data["watertight"])


class StreamingReadersTests(SimpleTestCase):
    """Os leitores em fluxo dão o mesmo resultado que o trimesh, com memória constante."""

    def test_binary_stl_stream_matches_trimesh(self):
        import trimesh

        data = trimesh.creation.icosphere(subdivisions=4, radius=30).export(file_type="stl")
        count = struct.unpack("<I", data[80:84])[0]
        stats = readers._stl_stats_binary(io.BytesIO(data[84:]), count)
        mesh = trimesh.load(io.BytesIO(data), file_type="stl")
        self.assertAlmostEqual(stats["volume"], mesh.volume, delta=mesh.volume * 1e-4)
        self.assertAlmostEqual(stats["area"], mesh.area, delta=mesh.area * 1e-4)
        for a, b in zip(stats["extents"], mesh.extents):
            self.assertAlmostEqual(a, b, places=3)

    def test_project_3mf_bbox_streaming(self):
        model = (
            '<?xml version="1.0"?><model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
            "<resources><object id=\"1\"><mesh><vertices>"
            + "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in [(0, 0, 0), (10, 0, 0), (0, 20, 5), (3, 4, 30)])
            + "</vertices></mesh></object></resources></model>"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("3D/3dmodel.model", model)
        buf.seek(0)
        self.assertEqual(readers.read_project_3mf(buf)["bbox_mm"], {"x": 10.0, "y": 20.0, "z": 30.0, "vertices": 4})
