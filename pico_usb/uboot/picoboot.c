// SPDX-License-Identifier: GPL-2.0+
/*
 * Minimal RP2350 picoboot client for a U-Boot USB host.
 *
 * This intentionally supports only the nonpersistent test path needed here:
 * copy an RP2350 no-flash image to SRAM, reboot into it, and collect the
 * resulting CDC self-test report.
 */

#include <common.h>
#include <command.h>
#include <cpu_func.h>
#include <dm.h>
#include <mapmem.h>
#include <memalign.h>
#include <usb.h>
#include <asm/byteorder.h>
#include <dm/device-internal.h>
#include <dm/uclass.h>
#include <linux/delay.h>
#include <linux/errno.h>

#define RPI_USB_VID		0x2e8a
#define PICO_SDK_PID		0x0009
#define RP2350_BOOT_PID		0x000f
#define USB_CDC_SET_LINE_CODING	0x20
#define USB_CDC_SET_CONTROL_LINE_STATE 0x22
#define USB_CDC_DTR		0x01
#define USB_CDC_RTS		0x02
#define PICO_RESET_PROTOCOL	0x01
#define PICO_RESET_REQUEST_BOOTSEL 0x01
#define PICOBOOT_MAGIC		0x431fd10b

#define PC_EXCLUSIVE_ACCESS	0x01
#define PC_WRITE		0x05
#define PC_REBOOT2		0x0a

#define REBOOT2_RAM_IMAGE	0x03
#define REBOOT2_PC_SP		0x0d
#define REBOOT2_TO_ARM		0x10
#define RP2350_SRAM_BASE	0x20000000
#define RP2350_SRAM_SIZE	0x00082000
/* RP2350 BOOTSEL FAT16 partition starts at LBA 1; data starts at LBA 0x124. */
#define RP2350_UF2_DATA_LBA	0x124

struct picoboot_range {
	__le32 addr;
	__le32 size;
} __packed;

struct picoboot_reboot2 {
	__le32 flags;
	__le32 delay_ms;
	__le32 param0;
	__le32 param1;
} __packed;

struct picoboot_cmd {
	__le32 magic;
	__le32 token;
	u8 cmd_id;
	u8 cmd_size;
	__le16 unused;
	__le32 transfer_length;
	union {
		u8 raw[16];
		struct picoboot_range range;
		struct picoboot_reboot2 reboot2;
	};
} __packed __aligned(4);

struct picoboot_link {
	struct usb_device *dev;
	unsigned int interface;
	unsigned int ep_out;
	unsigned int ep_in;
	u32 token;
};

struct cdc_line_coding {
	__le32 bit_rate;
	u8 stop_bits;
	u8 parity;
	u8 data_bits;
} __packed;

static bool is_rp2350(struct usb_device *dev)
{
	return dev &&
	       le16_to_cpu(dev->descriptor.idVendor) == RPI_USB_VID &&
	       (le16_to_cpu(dev->descriptor.idProduct) == RP2350_BOOT_PID ||
	        le16_to_cpu(dev->descriptor.idProduct) == PICO_SDK_PID);
}

static struct usb_device *find_rp2350(void)
{
#if CONFIG_IS_ENABLED(DM_USB)
	struct usb_device *udev;
	struct udevice *hub;
	struct uclass *uc;
	int ret;

	ret = uclass_get(UCLASS_USB_HUB, &uc);
	if (ret)
		return NULL;

	uclass_foreach_dev(hub, uc) {
		struct udevice *dev;

		if (!device_active(hub))
			continue;
		udev = dev_get_parent_priv(hub);
		if (is_rp2350(udev))
			return udev;

		for (device_find_first_child(hub, &dev);
		     dev;
		     device_find_next_child(&dev)) {
			if (!device_active(dev))
				continue;
			udev = dev_get_parent_priv(dev);
			if (is_rp2350(udev))
				return udev;
		}
	}
#else
	struct usb_device *dev;
	int i;

	for (i = 0; i < USB_MAX_DEVICE; i++) {
		dev = usb_get_dev_index(i);
		if (is_rp2350(dev))
			return dev;
	}
#endif

	return NULL;
}

static int find_picoboot_endpoints(struct picoboot_link *link)
{
	struct usb_interface *iface;
	struct usb_endpoint_descriptor *ep;
	int i, j;

	for (i = 0; i < link->dev->config.no_of_if; i++) {
		iface = &link->dev->config.if_desc[i];
		if (iface->desc.bInterfaceClass != 0xff ||
		    iface->desc.bInterfaceSubClass != 0 ||
		    iface->desc.bInterfaceProtocol != 0)
			continue;
		link->ep_out = 0;
		link->ep_in = 0;

		for (j = 0; j < iface->no_of_ep; j++) {
			ep = &iface->ep_desc[j];
			if ((ep->bmAttributes & USB_ENDPOINT_XFERTYPE_MASK) !=
			    USB_ENDPOINT_XFER_BULK)
				continue;
			if (ep->bEndpointAddress & USB_DIR_IN)
				link->ep_in = ep->bEndpointAddress & 0x0f;
			else
				link->ep_out = ep->bEndpointAddress & 0x0f;
		}

		if (link->ep_out && link->ep_in) {
			link->interface = iface->desc.bInterfaceNumber;
			return 0;
		}
	}

	return -ENODEV;
}

static int find_cdc_endpoints(struct picoboot_link *link,
			      unsigned int *control_interface)
{
	struct usb_interface *iface;
	struct usb_endpoint_descriptor *ep;
	bool have_control = false;
	int i, j;

	for (i = 0; i < link->dev->config.no_of_if; i++) {
		iface = &link->dev->config.if_desc[i];
		if (iface->desc.bInterfaceClass == USB_CLASS_COMM) {
			*control_interface = iface->desc.bInterfaceNumber;
			have_control = true;
			continue;
		}
		if (iface->desc.bInterfaceClass != USB_CLASS_CDC_DATA)
			continue;

		link->ep_out = 0;
		link->ep_in = 0;
		for (j = 0; j < iface->no_of_ep; j++) {
			ep = &iface->ep_desc[j];
			if ((ep->bmAttributes & USB_ENDPOINT_XFERTYPE_MASK) !=
			    USB_ENDPOINT_XFER_BULK)
				continue;
			if (ep->bEndpointAddress & USB_DIR_IN)
				link->ep_in = ep->bEndpointAddress & 0x0f;
			else
				link->ep_out = ep->bEndpointAddress & 0x0f;
		}
		if (link->ep_out && link->ep_in)
			link->interface = iface->desc.bInterfaceNumber;
	}

	return have_control && link->ep_out && link->ep_in ? 0 : -ENODEV;
}

static int enter_bootsel_via_cdc(struct usb_device *dev)
{
	ALLOC_CACHE_ALIGN_BUFFER(struct cdc_line_coding, coding, 1);
	struct usb_interface *iface;
	int i, ret;

	for (i = 0; i < dev->config.no_of_if; i++) {
		iface = &dev->config.if_desc[i];
		if (iface->desc.bInterfaceClass != 0xff ||
		    iface->desc.bInterfaceSubClass != 0 ||
		    iface->desc.bInterfaceProtocol != PICO_RESET_PROTOCOL)
			continue;

		printf("picoboot: requesting BOOTSEL through reset interface %u\n",
		       iface->desc.bInterfaceNumber);
		ret = usb_control_msg(
			dev, usb_sndctrlpipe(dev, 0),
			PICO_RESET_REQUEST_BOOTSEL,
			USB_TYPE_VENDOR | USB_RECIP_INTERFACE,
			0, iface->desc.bInterfaceNumber,
			NULL, 0, 1000);
		printf("picoboot: vendor reset request returned %d\n", ret);
		mdelay(1500);
		usb_stop();
		ret = usb_init();
		return ret;
	}

	for (i = 0; i < dev->config.no_of_if; i++) {
		iface = &dev->config.if_desc[i];
		if (iface->desc.bInterfaceClass != USB_CLASS_COMM)
			continue;

		memset(coding, 0, sizeof(*coding));
		coding->bit_rate = cpu_to_le32(1200);
		coding->data_bits = 8;
		printf("picoboot: requesting BOOTSEL through CDC interface %u\n",
		       iface->desc.bInterfaceNumber);
		ret = usb_control_msg(
			dev, usb_sndctrlpipe(dev, 0),
			USB_CDC_SET_CONTROL_LINE_STATE,
			USB_TYPE_CLASS | USB_RECIP_INTERFACE,
			USB_CDC_DTR | USB_CDC_RTS,
			iface->desc.bInterfaceNumber,
			NULL, 0, 1000);
		printf("picoboot: CDC DTR request returned %d\n", ret);
		ret = usb_control_msg(
			dev, usb_sndctrlpipe(dev, 0),
			USB_CDC_SET_LINE_CODING,
			USB_TYPE_CLASS | USB_RECIP_INTERFACE,
			0, iface->desc.bInterfaceNumber,
			coding, sizeof(*coding), 1000);
		printf("picoboot: CDC 1200-baud request returned %d\n", ret);
		mdelay(1500);
		usb_stop();
		ret = usb_init();
		if (ret)
			return ret;
		return 0;
	}

	return -ENODEV;
}

/*
 * U-Boot 2024.1 xHCI configures endpoints from if_desc[0] only. Present the
 * selected nonzero interface first and repeat SET_CONFIGURATION so its endpoint
 * rings are created.
 */
static int enable_selected_endpoints(struct picoboot_link *link)
{
	struct usb_interface original;
	struct usb_interface *iface;
	int ret;
	int i;

	for (i = 0; i < link->dev->config.no_of_if; i++) {
		iface = &link->dev->config.if_desc[i];
		if (iface->desc.bInterfaceNumber != link->interface)
			continue;
		if (i) {
			memcpy(&original, &link->dev->config.if_desc[0],
			       sizeof(original));
			memcpy(&link->dev->config.if_desc[0], iface,
			       sizeof(*iface));
		}
		ret = usb_control_msg(
			link->dev, usb_sndctrlpipe(link->dev, 0),
			USB_REQ_SET_CONFIGURATION, 0,
			link->dev->config.desc.bConfigurationValue,
			0, NULL, 0, USB_CNTL_TIMEOUT);
		if (i)
			memcpy(&link->dev->config.if_desc[0], &original,
			       sizeof(original));
		return ret;
	}

	return -ENODEV;
}

static int picoboot_command(struct picoboot_link *link,
			    struct picoboot_cmd *cmd, void *data)
{
	ALLOC_CACHE_ALIGN_BUFFER(u8, ack, USB_DMA_MINALIGN);
	int actual = 0;
	int ret;
	u32 offset;
	u32 transfer_length = le32_to_cpu(cmd->transfer_length);

	cmd->magic = cpu_to_le32(PICOBOOT_MAGIC);
	cmd->token = cpu_to_le32(++link->token);

	ret = usb_bulk_msg(link->dev,
			   usb_sndbulkpipe(link->dev, link->ep_out),
			   cmd, sizeof(*cmd), &actual, 3000);
	if (ret || actual != sizeof(*cmd)) {
		printf("picoboot: command %02x send failed (%d, %d/%zu)\n",
		       cmd->cmd_id, ret, actual, sizeof(*cmd));
		return ret ? ret : -EIO;
	}

	if (transfer_length) {
		for (offset = 0; offset < transfer_length; offset += 64) {
			u32 chunk = min_t(u32, 64, transfer_length - offset);

			actual = 0;
			ret = usb_bulk_msg(
				link->dev,
				usb_sndbulkpipe(link->dev, link->ep_out),
				(u8 *)data + offset, chunk, &actual, 3000);
			if (ret || actual != chunk) {
				printf("picoboot: data send failed at %u "
				       "(%d, %d/%u)\n",
				       offset, ret, actual, chunk);
				return ret ? ret : -EIO;
			}
		}
	}

	actual = 0;
	ret = usb_bulk_msg(link->dev,
			   usb_rcvbulkpipe(link->dev, link->ep_in),
			   ack, 1, &actual, 3000);
	if (ret || actual != 0) {
		printf("picoboot: command %02x ACK failed (%d, %d bytes)\n",
		       cmd->cmd_id, ret, actual);
		return ret ? ret : -EIO;
	}

	return 0;
}

static int picoboot_exclusive(struct picoboot_link *link)
{
	ALLOC_CACHE_ALIGN_BUFFER(struct picoboot_cmd, cmd, 1);

	memset(cmd, 0, sizeof(*cmd));
	cmd->cmd_id = PC_EXCLUSIVE_ACCESS;
	cmd->cmd_size = 1;
	cmd->raw[0] = 1;
	return picoboot_command(link, cmd, NULL);
}

static int picoboot_write(struct picoboot_link *link, ulong source,
			  u32 target, u32 size)
{
	ALLOC_CACHE_ALIGN_BUFFER(struct picoboot_cmd, cmd, 1);
	void *data = map_sysmem(source, size);
	int ret;

	memset(cmd, 0, sizeof(*cmd));
	cmd->cmd_id = PC_WRITE;
	cmd->cmd_size = sizeof(cmd->range);
	cmd->transfer_length = cpu_to_le32(size);
	cmd->range.addr = cpu_to_le32(target);
	cmd->range.size = cpu_to_le32(size);

	flush_dcache_range(source, ALIGN(source + size, ARCH_DMA_MINALIGN));
	ret = picoboot_command(link, cmd, data);
	unmap_sysmem(data);
	return ret;
}

static int picoboot_reboot_direct(struct picoboot_link *link, ulong source)
{
	ALLOC_CACHE_ALIGN_BUFFER(struct picoboot_cmd, cmd, 1);
	const __le32 *vectors = map_sysmem(source, 8);
	u32 stack_pointer = le32_to_cpu(vectors[0]);
	u32 program_counter = le32_to_cpu(vectors[1]);
	int ret;

	memset(cmd, 0, sizeof(*cmd));
	cmd->cmd_id = PC_REBOOT2;
	cmd->cmd_size = sizeof(cmd->reboot2);
	cmd->reboot2.flags = cpu_to_le32(REBOOT2_PC_SP | REBOOT2_TO_ARM);
	cmd->reboot2.delay_ms = cpu_to_le32(500);
	cmd->reboot2.param0 = cpu_to_le32(program_counter);
	cmd->reboot2.param1 = cpu_to_le32(stack_pointer);
	printf("picoboot: direct ARM reboot PC=%#x SP=%#x\n",
	       program_counter, stack_pointer);
	ret = picoboot_command(link, cmd, NULL);
	unmap_sysmem(vectors);
	return ret;
}

static int receive_pico_result(void)
{
	ALLOC_CACHE_ALIGN_BUFFER(u8, message, USB_DMA_MINALIGN);
	struct picoboot_link link = { 0 };
	unsigned int control_interface = 0;
	int actual = 0;
	int ret;

	mdelay(1500);
	usb_stop();
	ret = usb_init();
	if (ret)
		return ret;

	link.dev = find_rp2350();
	if (!link.dev) {
		printf("picoboot: test firmware did not enumerate\n");
		return -ENODEV;
	}
	printf("picoboot: Pico USB identity: %s\n", link.dev->prod);
	if (strstr(link.dev->prod, "PICO2_USB_SPI_PASS")) {
		printf("picoboot: SPI result verified through USB descriptor\n");
		return 0;
	}
	if (strstr(link.dev->prod, "PICO2_USB_SPI_FAIL"))
		return -EIO;

	ret = find_cdc_endpoints(&link, &control_interface);
	if (ret) {
		printf("picoboot: test firmware CDC interface not found\n");
		return ret;
	}
	ret = enable_selected_endpoints(&link);
	if (ret)
		return ret;

	ret = usb_control_msg(
		link.dev, usb_sndctrlpipe(link.dev, 0),
		USB_CDC_SET_CONTROL_LINE_STATE,
		USB_TYPE_CLASS | USB_RECIP_INTERFACE,
		USB_CDC_DTR | USB_CDC_RTS, control_interface,
		NULL, 0, 1000);
	if (ret < 0) {
		printf("picoboot: could not assert CDC DTR: %d\n", ret);
		return ret;
	}

	/*
	 * pico_stdio_usb starts producing output only after DTR is observed.
	 * Give its background TinyUSB task time to queue at least one report
	 * before submitting an xHCI bulk-IN transfer.
	 */
	mdelay(1200);
	memset(message, 0, USB_DMA_MINALIGN);
	ret = usb_bulk_msg(link.dev,
			   usb_rcvbulkpipe(link.dev, link.ep_in),
			   message, USB_DMA_MINALIGN - 1, &actual, 5000);
	if (ret || actual <= 0) {
		printf("picoboot: no result from Pico test firmware "
		       "(%d, %d bytes)\n", ret, actual);
		return ret ? ret : -ETIMEDOUT;
	}
	message[min_t(int, actual, USB_DMA_MINALIGN - 1)] = '\0';
	printf("picoboot: Pico reports: %s", message);
	if (!strstr((char *)message, "PICO2_USB_SPI_PASS"))
		return -EIO;

	return 0;
}

static int write_uf2_records(ulong source, ulong size)
{
	char command[80];
	ulong block_count = size / 512;
	ulong block;
	int ret;

	if (!block_count || size % 512)
		return -EINVAL;
	if (usb_stor_scan(1) < 0)
		return -ENODEV;

	flush_dcache_range(source, ALIGN(source + size, ARCH_DMA_MINALIGN));
	for (block = 0; block < block_count; block++) {
		snprintf(command, sizeof(command),
			 "usb write %lx %lx 1",
			 source + block * 512,
			 (ulong)RP2350_UF2_DATA_LBA + block);
		ret = run_command(command, 0);
		/*
		 * The final UF2 record may reboot the Pico before its SCSI status
		 * phase completes. Earlier record failures are always fatal.
		 */
		if (ret && block + 1 != block_count)
			return -EIO;
		if (!(block & 0xf))
			printf("picoboot: UF2 record %lu/%lu\n",
			       block + 1, block_count);
	}
	printf("picoboot: sent %lu UF2 records; waiting for RAM image\n",
	       block_count);
	return 0;
}

static int do_picoboot(struct cmd_tbl *cmdtp, int flag, int argc,
		       char *const argv[])
{
	struct picoboot_link link = { 0 };
	ulong source;
	ulong size;
	int ret;

	if (argc != 3)
		return CMD_RET_USAGE;

	source = hextoul(argv[1], NULL);
	size = hextoul(argv[2], NULL);
	if (!size || size > 4 * 1024 * 1024 || size % 512)
		return CMD_RET_USAGE;

	if (!usb_started) {
		ret = usb_init();
		if (ret)
			return CMD_RET_FAILURE;
	}

	link.dev = find_rp2350();
	if (!link.dev) {
		printf("picoboot: RP2350 device 2e8a:000f not found\n");
		return CMD_RET_FAILURE;
	}

	ret = find_picoboot_endpoints(&link);
	if (ret) {
		ret = enter_bootsel_via_cdc(link.dev);
		if (ret) {
			printf("picoboot: neither picoboot nor resettable CDC "
			       "interface found\n");
			return CMD_RET_FAILURE;
		}
		link.dev = find_rp2350();
		if (!link.dev) {
			printf("picoboot: RP2350 did not re-enumerate in BOOTSEL\n");
			return CMD_RET_FAILURE;
		}
		ret = find_picoboot_endpoints(&link);
		if (ret) {
			printf("picoboot: BOOTSEL vendor interface not found\n");
			return CMD_RET_FAILURE;
		}
	}

	printf("picoboot: RP2350 BOOTSEL ready; writing RAM-only UF2\n");
	ret = write_uf2_records(source, size);
	if (ret)
		goto fail;

	ret = receive_pico_result();
	if (ret)
		goto fail;

	printf("picoboot: USB load, execution, and SPI loopback verified\n");
	return CMD_RET_SUCCESS;

fail:
	printf("picoboot: failed: %d\n", ret);
	return CMD_RET_FAILURE;
}

U_BOOT_CMD(
	picoboot, 3, 0, do_picoboot,
	"load and execute an RP2350 RAM-only UF2 over USB",
	"<source-address> <hex-size>\n"
	"    - send one 512-byte UF2 record per BOOTSEL sector write"
);
