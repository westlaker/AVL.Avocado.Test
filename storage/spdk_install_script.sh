#!/bin/bash
#
# SPDK Installation Script for Storage Test Suite
# This script installs SPDK (Storage Performance Development Kit) for userspace NVMe testing
#
# Usage:
#   sudo ./install_spdk.sh [install_path]
#
# Default install path: /usr/local/src/spdk
#

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default installation path
INSTALL_PATH="${1:-/usr/local/src/spdk}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}SPDK Installation Script${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Installation path: $INSTALL_PATH"
echo "This script will:"
echo "  1. Install SPDK dependencies"
echo "  2. Clone SPDK repository"
echo "  3. Build SPDK"
echo "  4. Configure hugepages"
echo "  5. Verify installation"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Error: Please run as root (sudo)${NC}"
    exit 1
fi

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    echo -e "${RED}Cannot detect OS${NC}"
    exit 1
fi

echo -e "${YELLOW}Detected OS: $OS${NC}"
echo ""

# Step 1: Install dependencies
echo -e "${GREEN}[1/5] Installing SPDK dependencies...${NC}"

case $OS in
    ubuntu|debian)
        apt-get update
        apt-get install -y \
            git gcc g++ make \
            libaio-dev libssl-dev \
            uuid-dev libiscsi-dev \
            python3 python3-pip \
            libncurses5-dev libncursesw5-dev \
            pkg-config libnuma-dev \
            wget curl nasm meson \
            libcunit1-dev
        ;;
    
    centos|rhel|fedora)
        yum install -y \
            git gcc gcc-c++ make \
            libaio-devel openssl-devel \
            libuuid-devel libiscsi-devel \
            python3 python3-pip \
            ncurses-devel \
            pkgconfig numactl-devel \
            wget curl nasm meson \
            CUnit-devel
        ;;
    
    *)
        echo -e "${RED}Unsupported OS: $OS${NC}"
        echo "Please install dependencies manually"
        exit 1
        ;;
esac

echo -e "${GREEN}✓ Dependencies installed${NC}"
echo ""

# Step 2: Clone SPDK
echo -e "${GREEN}[2/5] Cloning SPDK repository...${NC}"

if [ -d "$INSTALL_PATH" ]; then
    echo -e "${YELLOW}Warning: $INSTALL_PATH already exists${NC}"
    read -p "Remove and reinstall? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$INSTALL_PATH"
    else
        echo "Using existing SPDK installation"
        cd "$INSTALL_PATH"
    fi
else
    mkdir -p "$(dirname "$INSTALL_PATH")"
    cd "$(dirname "$INSTALL_PATH")"
    
    echo "Cloning SPDK (this may take several minutes)..."
    git clone https://github.com/spdk/spdk.git "$(basename "$INSTALL_PATH")"
    cd "$(basename "$INSTALL_PATH")"
    
    # Use LTS version (v24.01 as of Jan 2026)
    echo "Checking out LTS version..."
    git checkout v24.01
    
    echo "Updating submodules..."
    git submodule update --init --recursive
fi

echo -e "${GREEN}✓ SPDK repository ready${NC}"
echo ""

# Step 3: Build SPDK
echo -e "${GREEN}[3/5] Building SPDK...${NC}"

cd "$INSTALL_PATH"

# Configure SPDK - use correct options for newer SPDK
echo "Configuring SPDK..."

# Try newer SPDK configure format first (v21.01+)
if ./configure --help | grep -q "with-shared"; then
    echo "Using newer SPDK configure options..."
    ./configure --with-shared
elif ./configure --help | grep -q "with-nvme"; then
    echo "Using older SPDK configure options..."
    ./configure --with-nvme
else
    echo "Using default SPDK configure..."
    ./configure
fi

# Build SPDK (use all CPU cores)
NUM_CORES=$(nproc)
echo "Building SPDK with $NUM_CORES cores (this takes 10-30 minutes)..."
make -j$NUM_CORES

# Check if build succeeded
if [ ! -f "build/bin/spdk_tgt" ] && [ ! -f "build/examples/perf" ]; then
    echo -e "${YELLOW}Warning: SPDK binaries not found in expected locations${NC}"
    echo "Searching for perf tool..."
    find . -name "*perf*" -type f -executable 2>/dev/null | head -5
fi

echo -e "${GREEN}✓ SPDK built successfully${NC}"
echo ""

# Step 4: Configure hugepages
echo -e "${GREEN}[4/5] Configuring hugepages...${NC}"

# Calculate hugepages (1GB = 512 x 2MB pages, we want 2GB)
HUGEPAGES=1024

echo "Setting up $HUGEPAGES hugepages (2GB total)..."

# Set hugepages for current session
echo $HUGEPAGES > /proc/sys/vm/nr_hugepages

# Make persistent across reboots
if ! grep -q "vm.nr_hugepages" /etc/sysctl.conf; then
    echo "vm.nr_hugepages = $HUGEPAGES" >> /etc/sysctl.conf
    echo "Added hugepages to /etc/sysctl.conf (persistent across reboots)"
else
    echo "Hugepages already configured in /etc/sysctl.conf"
fi

# Mount hugetlbfs if not already mounted
if ! mount | grep -q hugetlbfs; then
    echo "Mounting hugetlbfs..."
    mkdir -p /mnt/huge
    mount -t hugetlbfs nodev /mnt/huge
    echo "✓ Mounted hugetlbfs at /mnt/huge"
    
    # Make mount persistent
    if ! grep -q "hugetlbfs" /etc/fstab; then
        echo "nodev /mnt/huge hugetlbfs defaults 0 0" >> /etc/fstab
        echo "Added hugetlbfs to /etc/fstab (persistent across reboots)"
    fi
else
    echo "✓ hugetlbfs already mounted"
fi

# Verify hugepages
HUGE_FREE=$(cat /proc/meminfo | grep HugePages_Free | awk '{print $2}')
echo "HugePages available: $HUGE_FREE"

if [ "$HUGE_FREE" -lt 512 ]; then
    echo -e "${YELLOW}Warning: Less than 1GB of hugepages available${NC}"
    echo "You may need to reboot for hugepages to be properly allocated"
fi

echo -e "${GREEN}✓ Hugepages configured${NC}"
echo ""

# Step 5: Verify installation
echo -e "${GREEN}[5/5] Verifying SPDK installation...${NC}"

ERRORS=0

# Check for perf tool in various locations
PERF_FOUND=0
PERF_PATHS=(
    "$INSTALL_PATH/build/examples/perf"
    "$INSTALL_PATH/build/bin/spdk_nvme_perf"
    "$INSTALL_PATH/examples/nvme/perf/perf"
    "$INSTALL_PATH/app/spdk_nvme_perf/spdk_nvme_perf"
)

echo "Searching for SPDK perf tool..."
for perf_path in "${PERF_PATHS[@]}"; do
    if [ -f "$perf_path" ]; then
        echo "✓ Found perf tool: $perf_path"
        PERF_FOUND=1
        break
    fi
done

if [ $PERF_FOUND -eq 0 ]; then
    echo -e "${RED}✗ SPDK perf tool not found in standard locations${NC}"
    echo "Searching entire SPDK directory for perf binaries..."
    find "$INSTALL_PATH" -name "*perf*" -type f -executable 2>/dev/null | head -5
    ERRORS=$((ERRORS + 1))
fi

# Check if setup script exists
if [ -f "$INSTALL_PATH/scripts/setup.sh" ]; then
    echo "✓ SPDK setup script found"
else
    echo -e "${RED}✗ SPDK setup script not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

# Check hugepages
if [ "$HUGE_FREE" -gt 0 ]; then
    echo "✓ Hugepages available: $HUGE_FREE"
else
    echo -e "${YELLOW}⚠ No hugepages available (may need reboot)${NC}"
fi

echo ""

if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}SPDK Installation Complete!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "Installation location: $INSTALL_PATH"
    echo ""
    echo "To use SPDK with the storage test suite:"
    echo "  export SPDK_PATH=$INSTALL_PATH"
    echo "  export TEST_DEVICE=/dev/nvme0n1"
    echo "  sudo -E avocado run storage_test_suite.py"
    echo ""
    echo "To test SPDK manually:"
    echo "  cd $INSTALL_PATH"
    echo "  sudo ./scripts/setup.sh"
    echo "  sudo ./build/examples/perf -q 128 -o 4096 -w read -t 10 \\"
    echo "    -c 0x1 -r 'trtype:PCIe traddr:0000:01:00.0'"
    echo "  sudo ./scripts/setup.sh reset"
    echo ""
    
    if [ "$HUGE_FREE" -lt 512 ]; then
        echo -e "${YELLOW}Note: Hugepages may not be fully allocated.${NC}"
        echo -e "${YELLOW}If SPDK tests fail, try rebooting the system.${NC}"
        echo ""
    fi
    
    exit 0
else
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}SPDK Installation Failed!${NC}"
    echo -e "${RED}========================================${NC}"
    echo ""
    echo "Errors encountered: $ERRORS"
    echo "Please check the output above for details"
    echo ""
    exit 1
fi
