"""
Example client for testing the File Processing API.

This demonstrates how to:
1. Upload a file with metadata
2. Upload using S3 path
3. Check status of processing jobs
"""

import requests
import json
import time
import os

# API Configuration
BASE_URL = "http://localhost:8000"
IDENTIFICATION_URL = f"{BASE_URL}/identification"
STATUS_URL = f"{BASE_URL}/status"


def create_test_file(filename="test.txt", content="This is a test file for processing."):
    """Create a test file for upload."""
    with open(filename, "w") as f:
        f.write(content)
    print(f"Created test file: {filename}")
    return filename


def example_upload_file():
    """Example: Upload a file with metadata."""
    print("\n" + "="*60)
    print("Example 1: Upload file with metadata")
    print("="*60)
    
    # Create a test file
    test_file = create_test_file("sample.txt")
    
    try:
        # Prepare form data
        files = {
            'file': open(test_file, 'rb')
        }
        
        data = {
            'request_id': 'test_req_001',
            'split': 'true',
            'metadata': json.dumps({"key": "value", "environment": "test"})
        }
        
        # Make request
        print(f"\nSending POST request to {IDENTIFICATION_URL}")
        print(f"Request ID: {data['request_id']}")
        print(f"Split: {data['split']}")
        print(f"Metadata: {data['metadata']}")
        
        response = requests.post(IDENTIFICATION_URL, files=files, data=data)
        
        print(f"\nResponse Status: {response.status_code}")
        print(f"Response Body: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code == 202:
            request_id = response.json()['request_id']
            return request_id
        
    except Exception as e:
        print(f"Error: {e}")
    
    finally:
        # Cleanup
        if os.path.exists(test_file):
            os.remove(test_file)
            print(f"\nCleaned up test file: {test_file}")
    
    return None


def example_s3_path():
    """Example: Process file using S3 path."""
    print("\n" + "="*60)
    print("Example 2: Process file from S3 path")
    print("="*60)
    
    try:
        # Prepare form data (no file, just S3 path)
        data = {
            'request_id': 'test_req_002',
            'split': 'false',
            's3_path': 'https://s3.ap-south-1.amazonaws.com/bucket_name/test_req_002/raw/document.pdf',
            'metadata': json.dumps({"source": "s3", "bucket": "bucket_name"})
        }
        
        # Make request
        print(f"\nSending POST request to {IDENTIFICATION_URL}")
        print(f"Request ID: {data['request_id']}")
        print(f"S3 Path: {data['s3_path']}")
        print(f"Split: {data['split']}")
        print(f"Metadata: {data['metadata']}")
        
        response = requests.post(IDENTIFICATION_URL, data=data)
        
        print(f"\nResponse Status: {response.status_code}")
        print(f"Response Body: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code == 202:
            request_id = response.json()['request_id']
            return request_id
        
    except Exception as e:
        print(f"Error: {e}")
    
    return None


def check_status(request_id):
    """Check the status of a processing job."""
    print("\n" + "="*60)
    print(f"Checking status for request_id: {request_id}")
    print("="*60)
    
    try:
        url = f"{STATUS_URL}/{request_id}"
        response = requests.get(url)
        
        print(f"\nResponse Status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"\nStatus: {result['status']}")
            print(f"Request ID: {result['request_id']}")
            
            if result['results']:
                print(f"\nResults:")
                results_obj = json.loads(result['results'])
                print(json.dumps(results_obj, indent=2))
            else:
                print("\nResults: Not available yet (still processing)")
        else:
            print(f"Response Body: {response.json()}")
        
        return response.json()
        
    except Exception as e:
        print(f"Error: {e}")
    
    return None


def wait_for_completion(request_id, max_wait_seconds=30, poll_interval=5):
    """Wait for a job to complete."""
    print("\n" + "="*60)
    print(f"Waiting for job completion (max {max_wait_seconds}s)")
    print("="*60)
    
    elapsed = 0
    while elapsed < max_wait_seconds:
        status_data = check_status(request_id)
        
        if status_data and status_data['status'] not in ['Processing']:
            print(f"\nJob completed with status: {status_data['status']}")
            return status_data
        
        print(f"\nStill processing... waiting {poll_interval}s")
        time.sleep(poll_interval)
        elapsed += poll_interval
    
    print(f"\nMax wait time reached. Job may still be processing.")
    return None


def main():
    """Run example demonstrations."""
    print("\n" + "="*60)
    print("File Processing API - Example Client")
    print("="*60)
    print(f"Base URL: {BASE_URL}")
    
    # Check if API is available
    try:
        response = requests.get(f"{BASE_URL}/health")
        if response.status_code == 200:
            print("✓ API is healthy and ready")
        else:
            print("✗ API health check failed")
            return
    except Exception as e:
        print(f"✗ Cannot connect to API: {e}")
        return
    
    # Example 1: Upload file
    request_id_1 = example_upload_file()
    
    # Example 2: S3 path
    request_id_2 = example_s3_path()
    
    # Check status immediately
    if request_id_1:
        print("\n\nChecking status immediately after submission:")
        check_status(request_id_1)
    
    # Uncomment to wait for completion (takes 25+ seconds)
    # if request_id_1:
    #     wait_for_completion(request_id_1, max_wait_seconds=35, poll_interval=5)
    
    print("\n" + "="*60)
    print("Examples completed!")
    print("="*60)
    print("\nNote: Processing takes ~25 seconds.")
    print("Check status again in 30 seconds to see results.")
    if request_id_1:
        print(f"\ncurl -X GET \"{STATUS_URL}/{request_id_1}\"")
    if request_id_2:
        print(f"curl -X GET \"{STATUS_URL}/{request_id_2}\"")


if __name__ == "__main__":
    main()
