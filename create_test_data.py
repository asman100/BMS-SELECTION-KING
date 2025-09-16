#!/usr/bin/env python3
"""
Script to create test data for the controller selection feature.
This adds panels and equipment with I/O points based on the panels.csv data.
"""

import csv
import json
import requests
from requests import Session

def login_to_app():
    """Login to the application and return session."""
    session = Session()
    
    # Get login page to establish session
    response = session.get('http://localhost:5001/login')
    
    # Login with admin credentials
    login_data = {
        'username': 'admin',
        'password': 'admin123'
    }
    
    response = session.post('http://localhost:5001/login', json=login_data)
    data = response.json()
    
    if not data.get('success'):
        raise Exception(f"Login failed: {data.get('error', 'Unknown error')}")
    
    print("✓ Successfully logged in")
    return session

def create_test_project(session):
    """Create a test project."""
    project_data = {
        'name': 'Controller Test Project'
    }
    
    response = session.post('http://localhost:5001/projects', json=project_data)
    
    if response.status_code == 201:
        data = response.json()
        project_id = data['id']
        print(f"✓ Created test project with ID: {project_id}")
        return project_id
    else:
        # Maybe project already exists, try to get existing projects
        response = session.get('http://localhost:5001/api/projects')
        projects = response.json()
        for project in projects:
            if project['name'] == 'Controller Test Project':
                print(f"✓ Using existing test project with ID: {project['id']}")
                return project['id']
        
        raise Exception(f"Failed to create project: {response.text}")

def create_point_template(session, name, point_type):
    """Create a point template."""
    point_data = {
        'name': name,
        'quantity': 1,
        'sub_points': [{'point_type': point_type}]
    }
    
    response = session.post('http://localhost:5001/api/point_templates', json=point_data)
    if response.status_code == 201:
        data = response.json()
        print(f"✓ Created point template: {name} ({point_type})")
        return data['id']
    else:
        print(f"⚠ Failed to create point template {name}: {response.text}")
        return None

def create_equipment_template(session, name, type_key, point_ids):
    """Create an equipment template."""
    template_data = {
        'name': name,
        'type_key': type_key,
        'points': [{'point_template_id': pid, 'quantity': 1} for pid in point_ids]
    }
    
    response = session.post('http://localhost:5001/api/equipment_templates', json=template_data)
    if response.status_code == 201:
        data = response.json()
        print(f"✓ Created equipment template: {name}")
        return data['id']
    else:
        print(f"⚠ Failed to create equipment template {name}: {response.text}")
        return None

def add_panel_equipment(session, project_id, panel_name, floor, equipment_template_id, instance_name, point_ids):
    """Add equipment to a panel."""
    equipment_data = {
        'project_id': project_id,
        'panel_name': panel_name,
        'floor': floor,
        'equipment_template_id': equipment_template_id,
        'instance_name': instance_name,
        'quantity': 1,
        'selected_points': point_ids
    }
    
    response = session.post('http://localhost:5001/api/equipment', json=equipment_data)
    if response.status_code == 201:
        print(f"✓ Added equipment {instance_name} to panel {panel_name}")
        return True
    else:
        print(f"⚠ Failed to add equipment to {panel_name}: {response.text}")
        return False

def main():
    """Main function to create test data."""
    try:
        session = login_to_app()
        project_id = create_test_project(session)
        
        # Create point templates for different I/O types
        print("\n📋 Creating point templates...")
        ai_point_id = create_point_template(session, "Analog Input", "AI")
        ao_point_id = create_point_template(session, "Analog Output", "AO")
        di_point_id = create_point_template(session, "Digital Input", "DI")
        do_point_id = create_point_template(session, "Digital Output", "DO")
        ui_point_id = create_point_template(session, "Universal Input", "UI")
        
        point_ids = [pid for pid in [ai_point_id, ao_point_id, di_point_id, do_point_id, ui_point_id] if pid]
        
        if not point_ids:
            print("❌ Failed to create point templates")
            return
        
        # Create a generic equipment template
        print("\n🏗️ Creating equipment template...")
        equipment_template_id = create_equipment_template(session, "Generic I/O Equipment", "generic_io", point_ids)
        
        if not equipment_template_id:
            print("❌ Failed to create equipment template")
            return
        
        # Read panels.csv and create panels with I/O
        print("\n🏢 Creating panels with I/O based on panels.csv...")
        panels_data = []
        
        try:
            with open('panels.csv', 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    panels_data.append({
                        'name': row['PanelName'],
                        'floor': f"Floor-{row['PanelName'][-2:]}",  # Extract floor from panel name
                        'AI': int(row.get('AI', 0)),
                        'AO': int(row.get('AO', 0)),
                        'DI': int(row.get('DI', 0)),
                        'DO': int(row.get('DO', 0))
                    })
        except FileNotFoundError:
            print("❌ panels.csv not found. Creating sample panels instead...")
            panels_data = [
                {'name': 'LCP-01', 'floor': 'Ground Floor', 'AI': 5, 'AO': 3, 'DI': 27, 'DO': 8},
                {'name': 'LCP-02', 'floor': 'First Floor', 'AI': 8, 'AO': 4, 'DI': 54, 'DO': 16},
                {'name': 'LCP-03', 'floor': 'Second Floor', 'AI': 8, 'AO': 4, 'DI': 18, 'DO': 4},
            ]
        
        # Add equipment for each panel based on their I/O requirements
        for panel in panels_data:
            # Create multiple pieces of equipment to represent the I/O points
            instance_counter = 1
            
            # Add AI equipment
            for i in range(panel['AI']):
                add_panel_equipment(session, project_id, panel['name'], panel['floor'], 
                                  equipment_template_id, f"AI-{instance_counter}", [ai_point_id] if ai_point_id else [])
                instance_counter += 1
            
            # Add AO equipment
            for i in range(panel['AO']):
                add_panel_equipment(session, project_id, panel['name'], panel['floor'], 
                                  equipment_template_id, f"AO-{instance_counter}", [ao_point_id] if ao_point_id else [])
                instance_counter += 1
            
            # Add DI equipment (group in sets of 8 for efficiency)
            di_groups = (panel['DI'] + 7) // 8  # Round up to nearest 8
            for i in range(di_groups):
                count = min(8, panel['DI'] - i * 8)
                add_panel_equipment(session, project_id, panel['name'], panel['floor'], 
                                  equipment_template_id, f"DI-Group-{i+1}", [di_point_id] * count if di_point_id else [])
            
            # Add DO equipment (group in sets of 8 for efficiency)
            do_groups = (panel['DO'] + 7) // 8  # Round up to nearest 8
            for i in range(do_groups):
                count = min(8, panel['DO'] - i * 8)
                add_panel_equipment(session, project_id, panel['name'], panel['floor'], 
                                  equipment_template_id, f"DO-Group-{i+1}", [do_point_id] * count if do_point_id else [])
        
        print(f"\n✅ Test data creation completed!")
        print(f"📊 Access the controller selection at: http://localhost:5001/controller_selection/{project_id}")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()