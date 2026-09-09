#!/usr/bin/env python3
"""
NetBox Backup Upload Test with Progress Tracking

Usage:
    python test_upload.py /path/to/backup.json
"""

import sys
import time
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from core.backup_manager import save_netbox_backup


def progress_callback(message: str):
    """Print progress messages."""
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_upload.py /path/to/backup.json")
        sys.exit(1)
    
    backup_file = sys.argv[1]
    
    if not Path(backup_file).exists():
        print(f"Error: File not found: {backup_file}")
        sys.exit(1)
    
    file_size_mb = Path(backup_file).stat().st_size / (1024 * 1024)
    
    print("=" * 70)
    print("NetBox Backup Upload Test")
    print("=" * 70)
    print(f"File: {backup_file}")
    print(f"Size: {file_size_mb:.1f} MB")
    print()
    print("Starting upload with progress tracking...")
    print("-" * 70)
    
    start_time = time.time()
    
    try:
        with open(backup_file, 'rb') as f:
            result = save_netbox_backup(
                file_bytes=f,
                filename=Path(backup_file).name,
                enable_schema_discovery=False,  # Disabled for speed
                progress_callback=progress_callback
            )
        
        total_time = time.time() - start_time
        
        print("-" * 70)
        print()
        print("=" * 70)
        print("Upload Complete!")
        print("=" * 70)
        print(f"Total time:        {total_time:.1f}s")
        print(f"Records imported:  {result['total']:,}")
        print(f"Object types:      {result['object_types']}")
        print(f"Sites:             {result['sites']}")
        print(f"VLANs:             {result['ipam'].get('vlan', 0)}")
        print(f"Prefixes:          {result['ipam'].get('prefix', 0)}")
        print(f"Devices:           {result['devices']}")
        print(f"VMs:               {result['vms']}")
        print()
        
        if 'timings' in result:
            print("Performance breakdown:")
            print(f"  JSON parsing:    {result['timings']['parse']:.1f}s")
            print(f"  Data bucketing:  {result['timings']['bucket']:.1f}s")
            print(f"  DB ingestion:    {result['timings']['ingest']:.1f}s")
        
        print()
        print("Upload successful!")
        print()
        print("Next steps:")
        print("  1. Run schema discovery (optional):")
        print(f"     python field_manager.py discover {backup_file}")
        print()
        print("  2. View imported data in the NetBox Hub UI")
        print("=" * 70)
        
    except KeyboardInterrupt:
        print("\n\nUpload cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nError during upload: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
