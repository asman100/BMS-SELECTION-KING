#!/usr/bin/env python3
"""
Test script to verify equipment preset creation works with global templates.

This test verifies the fix for the issue where users received the error
"Equipment template not found or unauthorized" when saving presets.

The fix ensures that equipment templates (which are global) can be used
to create presets in any project, not just the project where the template
was originally created.
"""

import requests
import json
import time
import sys

def test_preset_with_global_templates():
    """Test that presets can be created using global equipment templates."""
    
    base_url = "http://localhost:5001"
    session = requests.Session()
    
    print("\n" + "="*70)
    print("Testing Equipment Preset Creation with Global Templates")
    print("="*70 + "\n")
    
    # Step 1: Login
    print("Step 1: Logging in...")
    login_response = session.post(f"{base_url}/login", json={
        "username": "admin",
        "password": "admin123"
    })
    
    if not login_response.json().get('success'):
        print("❌ FAILED: Could not login")
        return False
    
    print("✅ Login successful\n")
    
    # Step 2: Create Project A (where the template will be created)
    print("Step 2: Creating Project A...")
    timestamp = int(time.time())
    project_a_response = session.post(f"{base_url}/projects/create", json={
        "name": f"Project A - {timestamp}"
    })
    
    if not project_a_response.json().get('success'):
        print("❌ FAILED: Could not create Project A")
        return False
    
    project_a_id = int(project_a_response.json()['redirect'].split('=')[-1])
    print(f"✅ Project A created (ID: {project_a_id})\n")
    
    # Step 3: Create a point template in Project A
    print("Step 3: Creating point template in Project A...")
    point_data = {
        "name": f"AI Point {timestamp}",
        "quantity": 1,
        "sub_points": [{"name": "Analog Input", "point_type": "AI"}]
    }
    
    point_response = session.post(f"{base_url}/api/points/{project_a_id}", json=point_data)
    if point_response.status_code != 201:
        print(f"❌ FAILED: Could not create point template: {point_response.text}")
        return False
    
    point_id = point_response.json()['id']
    print(f"✅ Point template created (ID: {point_id})\n")
    
    # Step 4: Create an equipment template in Project A
    print("Step 4: Creating equipment template in Project A...")
    template_data = {
        "name": f"Test Equipment {timestamp}",
        "typeKey": f"test_equip_{timestamp}",
        "points": [{"id": point_id, "quantity": 1}]
    }
    
    template_response = session.post(f"{base_url}/api/equipment_templates/{project_a_id}", json=template_data)
    if template_response.status_code != 201:
        print(f"❌ FAILED: Could not create equipment template: {template_response.text}")
        return False
    
    template_dict = template_response.json()
    template_id = list(template_dict.values())[0]['id']
    template_name = list(template_dict.values())[0]['name']
    print(f"✅ Equipment template created (ID: {template_id}, Name: {template_name})\n")
    
    # Step 5: Create Project B (where we'll create the preset)
    print("Step 5: Creating Project B...")
    project_b_response = session.post(f"{base_url}/projects/create", json={
        "name": f"Project B - {timestamp}"
    })
    
    if not project_b_response.json().get('success'):
        print("❌ FAILED: Could not create Project B")
        return False
    
    project_b_id = int(project_b_response.json()['redirect'].split('=')[-1])
    print(f"✅ Project B created (ID: {project_b_id})\n")
    
    # Step 6: THE CRITICAL TEST - Create a preset in Project B using the template from Project A
    print("Step 6: Creating preset in Project B using template from Project A...")
    print("        (This is the test case that used to fail before the fix)")
    
    preset_data = {
        "name": f"Cross-Project Preset {timestamp}",
        "equipment_template_id": template_id,  # Template from Project A
        "quantity": 1,
        "selectedPoints": [point_id]
    }
    
    preset_response = session.post(f"{base_url}/api/projects/{project_b_id}/presets", json=preset_data)
    
    if preset_response.status_code == 201:
        preset = preset_response.json()
        print(f"✅ SUCCESS! Preset created in Project B")
        print(f"   Preset ID: {preset['id']}")
        print(f"   Preset Name: {preset['name']}")
        print(f"   Equipment Template: {preset['equipment_name']}")
        print(f"   Template ID: {preset['equipment_template_id']}")
        print(f"\n{'='*70}")
        print("✅ TEST PASSED: Equipment templates work as global resources!")
        print("   Presets can now be created using templates from any project.")
        print("="*70 + "\n")
        return True
    else:
        print(f"❌ FAILED! Status: {preset_response.status_code}")
        print(f"   Error: {preset_response.text}")
        print(f"\n{'='*70}")
        print("❌ TEST FAILED: The preset creation bug still exists")
        print("="*70 + "\n")
        return False

def main():
    """Run the test."""
    try:
        success = test_preset_with_global_templates()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
