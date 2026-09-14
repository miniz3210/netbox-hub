"""
Unit tests for dynamic network interface name parser.

Tests cover multiple vendors (Cisco, Huawei, Arista, etc.) and various
interface types including edge cases and sub-interfaces.
"""

import pytest
from utils.formatters import normalize_port_shortname, _get_interface_abbreviation


class TestInterfaceAbbreviation:
    """Test the dynamic abbreviation extraction logic."""
    
    def test_known_mappings(self):
        """Test known interface type mappings."""
        assert _get_interface_abbreviation("XGigabitEthernet") == "XGE"
        assert _get_interface_abbreviation("TenGigabitEthernet") == "Te"
        assert _get_interface_abbreviation("GigabitEthernet") == "Gi"
        assert _get_interface_abbreviation("FastEthernet") == "Fa"
        assert _get_interface_abbreviation("FortyGigabitEthernet") == "Fo"
        assert _get_interface_abbreviation("HundredGigE") == "Hu"
    
    def test_case_insensitive(self):
        """Test that abbreviation works regardless of case."""
        assert _get_interface_abbreviation("tengigabitethernet") == "Te"
        assert _get_interface_abbreviation("TENGIGABITETHERNET") == "Te"
        assert _get_interface_abbreviation("TenGigabitEthernet") == "Te"
    
    def test_abbreviated_forms(self):
        """Test already-abbreviated interface types."""
        assert _get_interface_abbreviation("TenGigE") == "Te"
        assert _get_interface_abbreviation("GigE") == "Gi"
        assert _get_interface_abbreviation("XGigE") == "XGE"
    
    def test_port_channel(self):
        """Test port-channel abbreviation."""
        assert _get_interface_abbreviation("Port-channel") == "Po"
        assert _get_interface_abbreviation("PortChannel") == "Po"
    
    def test_high_speed_interfaces(self):
        """Test high-speed interface abbreviations."""
        assert _get_interface_abbreviation("TwentyFiveGigE") == "Twe"
        assert _get_interface_abbreviation("TwentyFiveGigabitEthernet") == "Twe"
        assert _get_interface_abbreviation("HundredGigabitEthernet") == "Hu"


class TestNormalizePortShortname:
    """Test the main interface name normalization function."""
    
    # Huawei XGigabitEthernet (Common in Huawei switches)
    def test_huawei_xge_interfaces(self):
        """Test Huawei XGigabitEthernet formats."""
        assert normalize_port_shortname("XGigabitEthernet0/0/31") == "XGE0/0/31"
        assert normalize_port_shortname("XGigabitEthernet0/0/1") == "XGE0/0/1"
        assert normalize_port_shortname("XGigabitEthernet1/0/24") == "XGE1/0/24"
    
    # Cisco TenGigabitEthernet
    def test_cisco_ten_gig_interfaces(self):
        """Test Cisco TenGigabitEthernet formats."""
        assert normalize_port_shortname("TenGigabitEthernet1/0/1") == "Te1/0/1"
        assert normalize_port_shortname("TenGigabitEthernet0/0/48") == "Te0/0/48"
        assert normalize_port_shortname("TenGigE1/1/1") == "Te1/1/1"
    
    # Cisco GigabitEthernet
    def test_cisco_gig_interfaces(self):
        """Test Cisco GigabitEthernet formats."""
        assert normalize_port_shortname("GigabitEthernet1/0/24") == "Gi1/0/24"
        assert normalize_port_shortname("GigabitEthernet0/0/1") == "Gi0/0/1"
        assert normalize_port_shortname("GigE1/0/1") == "Gi1/0/1"
    
    # Cisco FastEthernet (legacy)
    def test_cisco_fast_ethernet(self):
        """Test Cisco FastEthernet formats."""
        assert normalize_port_shortname("FastEthernet0/1") == "Fa0/1"
        assert normalize_port_shortname("FastEthernet1/0/24") == "Fa1/0/24"
    
    # Cisco FortyGigabitEthernet
    def test_cisco_forty_gig(self):
        """Test Cisco FortyGigabitEthernet formats."""
        assert normalize_port_shortname("FortyGigabitEthernet0/1") == "Fo0/1"
        assert normalize_port_shortname("FortyGigabitEthernet1/0/1") == "Fo1/0/1"
    
    # High-speed interfaces
    def test_high_speed_interfaces(self):
        """Test 25G, 100G, and higher speed interfaces."""
        assert normalize_port_shortname("TwentyFiveGigE1/0/1") == "Twe1/0/1"
        assert normalize_port_shortname("HundredGigE0/0/1") == "Hu0/0/1"
        assert normalize_port_shortname("HundredGigabitEthernet1/0/1") == "Hu1/0/1"
    
    # Port-channel (LAG)
    def test_port_channel(self):
        """Test port-channel interfaces."""
        assert normalize_port_shortname("Port-channel10") == "Po10"
        assert normalize_port_shortname("Port-channel1") == "Po1"
        assert normalize_port_shortname("port-channel100") == "Po100"
    
    # Sub-interfaces
    def test_sub_interfaces(self):
        """Test sub-interface formats with VLAN tags."""
        assert normalize_port_shortname("XGigabitEthernet0/0/31.100") == "XGE0/0/31.100"
        assert normalize_port_shortname("TenGigabitEthernet1/0/1.200") == "Te1/0/1.200"
        assert normalize_port_shortname("GigabitEthernet1/0/1.999") == "Gi1/0/1.999"
    
    # Already shortened inputs
    def test_already_shortened(self):
        """Test that already-shortened interfaces pass through unchanged."""
        assert normalize_port_shortname("XGE0/0/31") == "XGE0/0/31"
        assert normalize_port_shortname("Te1/0/1") == "Te1/0/1"
        assert normalize_port_shortname("Gi1/0/24") == "Gi1/0/24"
        assert normalize_port_shortname("Fa0/1") == "Fa0/1"
        assert normalize_port_shortname("Po10") == "Po10"
    
    # Already shortened with sub-interfaces
    def test_already_shortened_with_subinterface(self):
        """Test shortened interfaces with sub-interface tags."""
        assert normalize_port_shortname("XGE0/0/31.100") == "XGE0/0/31.100"
        assert normalize_port_shortname("Te1/0/1.200") == "Te1/0/1.200"
    
    # Different slot/port formats
    def test_various_port_formats(self):
        """Test different port numbering schemes."""
        # Single number
        assert normalize_port_shortname("GigabitEthernet1") == "Gi1"
        # Two-level
        assert normalize_port_shortname("GigabitEthernet0/1") == "Gi0/1"
        # Three-level (common in chassis switches)
        assert normalize_port_shortname("GigabitEthernet1/0/24") == "Gi1/0/24"
    
    # Whitespace handling
    def test_whitespace_removal(self):
        """Test that whitespace is properly removed."""
        assert normalize_port_shortname("Gigabit Ethernet 1/0/24") == "Gi1/0/24"
        assert normalize_port_shortname("Ten GigabitEthernet 1/0/1") == "Te1/0/1"
        assert normalize_port_shortname(" XGigabitEthernet0/0/31 ") == "XGE0/0/31"
    
    # Management interfaces
    def test_management_interfaces(self):
        """Test management interface abbreviations."""
        assert normalize_port_shortname("Management1") == "Mgmt1"
        assert normalize_port_shortname("Management0/0") == "Mgmt0/0"
    
    # Edge cases
    def test_edge_cases(self):
        """Test edge cases and error handling."""
        # Empty string
        assert normalize_port_shortname("") == ""
        # Whitespace only
        assert normalize_port_shortname("   ") == ""
        # No port path (just interface type) - should remain as-is
        assert normalize_port_shortname("Ethernet") == "Ethernet"
    
    # Case sensitivity
    def test_case_sensitivity(self):
        """Test that parsing works with various case formats."""
        assert normalize_port_shortname("tengigabitethernet1/0/1") == "Te1/0/1"
        assert normalize_port_shortname("TENGIGABITETHERNET1/0/1") == "Te1/0/1"
        assert normalize_port_shortname("TenGigabitEthernet1/0/1") == "Te1/0/1"


class TestRealWorldExamples:
    """Test with real-world interface names from actual network devices."""
    
    def test_huawei_switch_examples(self):
        """Test real Huawei switch interface names."""
        # CloudEngine series
        assert normalize_port_shortname("XGigabitEthernet0/0/1") == "XGE0/0/1"
        assert normalize_port_shortname("XGigabitEthernet0/0/48") == "XGE0/0/48"
        assert normalize_port_shortname("GigabitEthernet0/0/1") == "Gi0/0/1"
    
    def test_cisco_catalyst_examples(self):
        """Test real Cisco Catalyst switch interface names."""
        # Catalyst 9000 series
        assert normalize_port_shortname("TenGigabitEthernet1/0/1") == "Te1/0/1"
        assert normalize_port_shortname("TenGigabitEthernet1/0/48") == "Te1/0/48"
        assert normalize_port_shortname("GigabitEthernet1/0/1") == "Gi1/0/1"
        assert normalize_port_shortname("FortyGigabitEthernet1/1/1") == "Fo1/1/1"
    
    def test_cisco_nexus_examples(self):
        """Test Cisco Nexus switch interface names."""
        assert normalize_port_shortname("Ethernet1/1") == "Eth1/1"
        assert normalize_port_shortname("Ethernet1/48") == "Eth1/48"
    
    def test_arista_examples(self):
        """Test Arista switch interface names."""
        # Arista typically uses Ethernet notation
        assert normalize_port_shortname("Ethernet1") == "Eth1"
        assert normalize_port_shortname("Ethernet49/1") == "Eth49/1"
    
    def test_mixed_vendor_uplink_scenario(self):
        """Test real-world uplink scenario with mixed vendors."""
        # Local: Huawei
        local_port = normalize_port_shortname("XGigabitEthernet0/0/31")
        # Remote: Cisco
        remote_port = normalize_port_shortname("TenGigabitEthernet1/0/1")
        
        assert local_port == "XGE0/0/31"
        assert remote_port == "Te1/0/1"
        
        # Uplink description format
        uplink = f"to RemoteDevice_{remote_port} [Uplink]"
        assert uplink == "to RemoteDevice_Te1/0/1 [Uplink]"


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
