/* Minimal reserved-DDR diagnostic for the DAQ Linux initramfs. */

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define PAGE_BYTES 4096u

int main(int argc, char **argv)
{
    uint64_t address;
    uint32_t aligned;
    uint32_t offset;
    uint32_t value;
    volatile uint32_t *word;
    void *mapping;
    char *end;
    int fd;

    if (argc != 2 && argc != 3) {
        fprintf(stderr, "usage: daq-mem ADDRESS [VALUE]\n");
        return 2;
    }
    errno = 0;
    address = strtoull(argv[1], &end, 0);
    if (errno != 0 || *end != '\0' || address > UINT32_MAX ||
        (address & 3u) != 0u) {
        fprintf(stderr, "daq-mem: invalid aligned 32-bit address: %s\n", argv[1]);
        return 2;
    }

    fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (fd < 0) {
        perror("daq-mem: /dev/mem");
        return 1;
    }
    aligned = (uint32_t)address & ~(PAGE_BYTES - 1u);
    offset = (uint32_t)address - aligned;
    mapping = mmap(NULL, PAGE_BYTES, PROT_READ | PROT_WRITE, MAP_SHARED, fd,
                   (off_t)aligned);
    if (mapping == MAP_FAILED) {
        fprintf(stderr, "daq-mem: mmap 0x%08" PRIx64 ": %s\n",
                address, strerror(errno));
        close(fd);
        return 1;
    }
    word = (volatile uint32_t *)((uint8_t *)mapping + offset);

    if (argc == 3) {
        errno = 0;
        value = (uint32_t)strtoul(argv[2], &end, 0);
        if (errno != 0 || *end != '\0') {
            fprintf(stderr, "daq-mem: invalid value: %s\n", argv[2]);
            munmap(mapping, PAGE_BYTES);
            close(fd);
            return 2;
        }
        *word = value;
        __atomic_thread_fence(__ATOMIC_SEQ_CST);
    }
    value = *word;
    printf("0x%08" PRIx32 "\n", value);

    munmap(mapping, PAGE_BYTES);
    close(fd);
    return 0;
}
