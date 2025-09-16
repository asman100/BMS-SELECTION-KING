#!/usr/bin/env python3
"""
Quick test script to create a panel with AI points to test AS-P server solutions.
"""

import requests
import json
import time

def test_asp_solution():
    """Test AS-P solution generation with AI requirements."""
    
    base_url = "http://localhost:5001"
    session = requests.Session()
    
    # Use timestamp to make unique names
    timestamp = int(time.time())
    
    # Login
    login_response = session.post(f"{base_url}/login", json={
        "username": "admin",
        "password": "admin123"
    })
    
    if not login_response.json().get('success'):
        print("❌ Login failed")
        return
    
    print("✅ Logged in successfully")
    
    # Create a test point template with AI type
    point_data = {
        "name": f"Test AI Point {timestamp}",
        "quantity": 20,  # 20 AI points to force AS-P solution
        "sub_points": [
            {"name": "Analog Input", "point_type": "AI"}
        ]
    }
    
    point_response = session.post(f"{base_url}/api/points/1", json=point_data)
    if point_response.status_code != 201:
        print(f"❌ Failed to create point template: {point_response.text}")
        return
    
    print("✅ Created AI point template")
    point_id = point_response.json()['id']
    
    # Create equipment template
    template_data = {
        "name": f"High AI Equipment {timestamp}",
        "typeKey": f"high_ai_equipment_{timestamp}",
        "points": [{"id": point_id, "quantity": 1}]
    }
    
    template_response = session.post(f"{base_url}/api/equipment_templates/1", json=template_data)
    if template_response.status_code != 201:
        print(f"❌ Failed to create equipment template: {template_response.text}")
        return
    
    print("✅ Created equipment template")
    template_data_response = template_response.json()
    template_id = list(template_data_response.keys())[0]  # Get the first key which is the template ID
    print(f"Template ID: {template_id}")
    
    # Add equipment to create a panel with AI points
    equipment_data = {
        "project_id": 1,
        "panelName": "LP-AI-TEST",
        "floor": "Test Floor",
        "equipment_template_id": int(template_id),
        "instanceName": "AI-TEST-01",
        "quantity": 1,
        "selectedPoints": [point_id],
        "type": f"high_ai_equipment_{timestamp}"
    }
    
    equipment_response = session.post(f"{base_url}/api/equipment/1", json=equipment_data)
    if equipment_response.status_code != 201:
        print(f"❌ Failed to create equipment: {equipment_response.text}")
        return
    
    print("✅ Created equipment with 20 AI points")
    
    # Test controller selection to see if AS-P solution appears
    controller_response = session.get(f"{base_url}/api/projects/1/controller_selection")
    if controller_response.status_code != 200:
        print(f"❌ Failed to get controller selection: {controller_response.text}")
        return
    
    data = controller_response.json()
    print(f"✅ Got controller selection data")
    
    # Check if AS-P solutions are present
    server_solutions = data.get('server_solutions', {})
    test_panel_found = False
    
    for panel_id, solutions in server_solutions.items():
        panel_info = next((p for p in data['panels'] if p['id'] == int(panel_id)), None)
        if panel_info and panel_info['name'] == 'LP-AI-TEST':
            test_panel_found = True
            print(f"\n📊 Panel: {panel_info['name']}")
            print(f"   AI Points: {panel_info['points'].get('AI', 0)}")
            print(f"   Solutions found: {len(solutions)}")
            
            asp_found = False
            asb_found = False
            
            for solution in solutions:
                print(f"   - {solution['description']}: ${solution['total_cost']:.2f}")
                if solution['type'] == 'AS-P':
                    asp_found = True
                    print(f"     Modules: {len(solution['modules'])}")
                elif solution['type'] == 'AS-B':
                    asb_found = True
            
            if asp_found and asb_found:
                print("✅ SUCCESS: Both AS-P and AS-B solutions are now available!")
            elif asp_found:
                print("⚠️  AS-P solution found, but no AS-B solutions")
            elif asb_found:
                print("⚠️  AS-B solutions found, but AS-P solution missing")
            else:
                print("❌ No server solutions found")
    
    if not test_panel_found:
        print("❌ Test panel not found in controller selection data")
    
    print(f"\n🌐 Test the interface at: {base_url}/controller_selection/1")

if __name__ == "__main__":
    test_asp_solution()