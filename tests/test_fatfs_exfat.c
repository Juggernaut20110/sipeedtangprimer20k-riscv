#include <stdio.h>
#include <stdint.h>
#include <assert.h>
#include <string.h>
#include "../firmware/peripherals/fatfs/ff.h"
#include "../firmware/peripherals/fatfs/diskio.h"
static FILE *image;
static DSTATUS init(BYTE drive) { return drive == 0 ? 0 : STA_NODISK; }
static DRESULT read_sectors(BYTE drive,BYTE *buffer,LBA_t sector,UINT count) {
    if(drive || fseek(image,(long)sector*512,SEEK_SET))return RES_ERROR;
    return fread(buffer,512,count,image)==count ? RES_OK : RES_ERROR;
}
static DRESULT write_sectors(BYTE drive,const BYTE *buffer,LBA_t sector,UINT count) {
    if(drive || fseek(image,(long)sector*512,SEEK_SET))return RES_ERROR;
    return fwrite(buffer,512,count,image)==count ? RES_OK : RES_ERROR;
}
static DRESULT control(BYTE drive,BYTE command,void *buffer) {
    if(drive)return RES_PARERR;
    if(command==CTRL_SYNC)return fflush(image)==0 ? RES_OK : RES_ERROR;
    if(command==GET_SECTOR_COUNT){*(LBA_t*)buffer=64*1024*1024/512;return RES_OK;}
    if(command==GET_SECTOR_SIZE){*(WORD*)buffer=512;return RES_OK;}
    if(command==GET_BLOCK_SIZE){*(DWORD*)buffer=1;return RES_OK;}
    return RES_PARERR;
}
static DISKOPS ops={init,init,read_sectors,write_sectors,control};
extern DISKOPS *FfDiskOps;
static uint8_t data[512],actual[512];
static void verify(const char *name,unsigned size,unsigned seed) {
    FIL file;UINT done;assert(f_open(&file,name,FA_CREATE_NEW|FA_WRITE)==FR_OK);
    for(unsigned offset=0;offset<size;offset+=512){
        for(unsigned i=0;i<512;++i)data[i]=(uint8_t)((offset+i)*37+((offset+i)>>8)+seed);
        unsigned count=size-offset;if(count>512)count=512;
        assert(f_write(&file,data,count,&done)==FR_OK && done==count);
    }
    assert(f_sync(&file)==FR_OK);assert(f_close(&file)==FR_OK);
    assert(f_open(&file,name,FA_READ)==FR_OK && f_size(&file)==size);
    for(unsigned offset=0;offset<size;offset+=512){
        unsigned count=size-offset;if(count>512)count=512;
        for(unsigned i=0;i<count;++i)data[i]=(uint8_t)((offset+i)*37+((offset+i)>>8)+seed);
        assert(f_read(&file,actual,count,&done)==FR_OK && done==count && !memcmp(data,actual,count));
    }
    assert(f_close(&file)==FR_OK);
    assert(f_open(&file,name,FA_CREATE_NEW|FA_WRITE)==FR_EXIST);
}
int main(int argc,char **argv) {
    assert(argc==2);FfDiskOps=&ops;image=fopen(argv[1],"r+b");assert(image);FATFS fs;FIL file;
    assert(f_mount(&fs,"0:",1)==FR_OK && fs.fs_type==FS_EXFAT);
    verify("0:/PRESERVE.BIN",512,23);
    assert(f_mkdir("0:/T20KTEST")==FR_OK);
    verify("0:/T20KTEST/SMALL.BIN",512,7);
    verify("0:/T20KTEST/LONG café 4096.BIN",4096,8);
    verify("0:/T20KTEST/ONE_MIB.BIN",1048576,9);
    assert(f_mount(NULL,"0:",0)==FR_OK);assert(f_mount(&fs,"0:",1)==FR_OK);
    assert(f_open(&file,"0:/PRESERVE.BIN",FA_READ)==FR_OK);UINT done;
    assert(f_read(&file,actual,512,&done)==FR_OK && done==512);
    for(unsigned i=0;i<512;++i)assert(actual[i]==(uint8_t)(i*37+(i>>8)+23));
    assert(f_close(&file)==FR_OK);assert(f_mount(NULL,"0:",0)==FR_OK);assert(fclose(image)==0);
    puts("exFAT create/sync/close/reopen and unrelated-file preservation passed");return 0;
}
