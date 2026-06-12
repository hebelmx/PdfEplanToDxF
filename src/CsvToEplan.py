import csv
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom

def csv_to_eplan_xml(csv_filepath, xml_filepath):
    # Create the root EPLAN data structure
    root = ET.Element("EplanPlcData", version="1.0")
    plc_project = ET.SubElement(root, "PlcProject", name="Imported_IO_Project")
    
    with open(csv_filepath, mode='r', encoding='utf-8') as csv_file:
        reader = csv.DictReader(csv_file)
        
        for row in reader:
            # Create a station/rack entry for each device tag
            plc_station = ET.SubElement(plc_project, "PlcStation", name=row['DeviceTag'])
            plc_card = ET.SubElement(plc_station, "PlcCard", rack=row['Rack'], slot=row['Slot'])
            
            # Map the connection point data
            connection = ET.SubElement(plc_card, "ConnectionPoint")
            ET.SubElement(connection, "Number").text = row['ConnectionPoint']
            ET.SubElement(connection, "Address").text = row['Address']
            ET.SubElement(connection, "DataType").text = row['DataType']
            ET.SubElement(connection, "SymbolicName").text = row['SymbolicName']
            ET.SubElement(connection, "FunctionText").text = row['FunctionText']

    # Format the XML into a readable, indented string
    xml_str = ET.tostring(root, encoding='utf-8')
    parsed_xml = minidom.parseString(xml_str)
    pretty_xml_str = parsed_xml.toprettyxml(indent="    ")

    with open(xml_filepath, "w", encoding="utf-8") as xml_file:
        xml_file.write(pretty_xml_str)

# Run the converter
csv_to_eplan_xml("io_list.csv", "eplan_io_import.xml")
print("Conversion complete! 'eplan_io_import.xml' is ready for EPLAN.")
