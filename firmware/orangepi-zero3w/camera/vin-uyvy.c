/* A733 VIN NV12 capture -> packed UYVY on a V4L2 loopback device. */
#include <linux/videodev2.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/poll.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
static void check(int result, const char *operation) {
    if (result < 0) { perror(operation); exit(1); }
}
static void xioctl(int fd, unsigned long cmd, void *arg, const char *name) {
    int r; do { r=ioctl(fd, cmd, arg); } while(r<0 && errno==EINTR);
    check(r, name);
}
static void send_all(int output, const void *data, size_t n) {
    const uint8_t *p=data;
    while(n) { ssize_t k=write(output,p,n); if(k<0 && errno==EINTR)continue;
        if(k<=0)exit(1);
        p+=k; n-=k;
    }
}
int main(int argc,char **argv) {
    if(argc!=5)return 2;
    unsigned w=atoi(argv[1]), h=atoi(argv[2]), fps=atoi(argv[3]);
    if(!((w==1920 && h==1080)||(w==1280 && h==720)||(w==640 && h==360)) || !(fps==15 || fps==30))return 2;
    int output=open(argv[4],O_RDWR);check(output,"open loopback");
    struct v4l2_format out={.type=V4L2_BUF_TYPE_VIDEO_OUTPUT};
    out.fmt.pix.width=w;out.fmt.pix.height=h;
    out.fmt.pix.pixelformat=V4L2_PIX_FMT_UYVY;out.fmt.pix.field=V4L2_FIELD_NONE;
    xioctl(output,VIDIOC_S_FMT,&out,"set loopback format");
    if(out.fmt.pix.width!=w || out.fmt.pix.height!=h || out.fmt.pix.pixelformat!=V4L2_PIX_FMT_UYVY)return 3;
    struct v4l2_streamparm rate={.type=V4L2_BUF_TYPE_VIDEO_OUTPUT};
    rate.parm.output.timeperframe=(struct v4l2_fract){1,fps};
    xioctl(output,VIDIOC_S_PARM,&rate,"set loopback rate");
    int fd=open("/dev/video0",O_RDWR|O_NONBLOCK);check(fd,"open video0");
    int input=0; xioctl(fd,VIDIOC_S_INPUT,&input,"select camera");
    enum v4l2_buf_type type=V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    struct v4l2_streamparm parm={.type=type};
    parm.parm.capture.timeperframe=(struct v4l2_fract){1,fps};parm.parm.capture.capturemode=2;
    xioctl(fd,VIDIOC_S_PARM,&parm,"set capture rate");
    struct v4l2_format fmt={.type=type};struct v4l2_pix_format_mplane *pix=&fmt.fmt.pix_mp;
    pix->width=w;pix->height=h;pix->pixelformat=V4L2_PIX_FMT_NV12;pix->field=V4L2_FIELD_NONE;
    xioctl(fd,VIDIOC_S_FMT,&fmt,"set NV12 format");
    if(pix->width!=w || pix->height!=h || pix->pixelformat!=V4L2_PIX_FMT_NV12 || pix->num_planes!=1)return 3;
    struct v4l2_requestbuffers req={.count=4,.type=type,.memory=V4L2_MEMORY_MMAP};
    xioctl(fd,VIDIOC_REQBUFS,&req,"allocate capture buffers");
    void *maps[4];size_t lengths[4];
    if(req.count<2 || req.count>4)return 4;
    for(unsigned i=0;i<req.count;i++) {
        struct v4l2_plane plane={0};
        struct v4l2_buffer b={.type=type,.memory=req.memory,.index=i,.length=1,.m.planes=&plane};
        xioctl(fd,VIDIOC_QUERYBUF,&b,"query buffer");
        lengths[i]=plane.length;
        maps[i]=mmap(NULL,plane.length,PROT_READ|PROT_WRITE,MAP_SHARED,fd,plane.m.mem_offset);
        if(maps[i]==MAP_FAILED){perror("map buffer");return 1;}
        xioctl(fd,VIDIOC_QBUF,&b,"queue buffer");
    }
    uint32_t size=w*h*2;uint8_t *packed=malloc(size);
    if(!packed)return 1;
    xioctl(fd,VIDIOC_STREAMON,&type,"start camera");
    for(;;){
        struct pollfd pol={.fd=fd,.events=POLLIN};int r=poll(&pol,1,4000);
        if(r<=0)return 5;
        struct v4l2_plane plane={0};struct v4l2_buffer b={.type=type,.memory=req.memory,.length=1,.m.planes=&plane};
        xioctl(fd,VIDIOC_DQBUF,&b,"dequeue buffer");
        size_t valid=w*h*3/2;
        if(b.index>=req.count || plane.data_offset>lengths[b.index] || valid>lengths[b.index]-plane.data_offset || plane.bytesused<valid+plane.data_offset)return 6;
        if(!(b.flags&V4L2_BUF_FLAG_ERROR)){
            const uint8_t *y=(const uint8_t*)maps[b.index]+plane.data_offset;
            const uint8_t *uv=y+w*h; /* VIN pads the end of the frame, not the image rows. */
            for(unsigned row=0;row<h;row++){
                uint8_t *dst=packed+row*w*2;
                for(unsigned x=0;x<w;x+=2){dst[2*x]=uv[(row/2)*w+x];dst[2*x+1]=y[row*w+x];dst[2*x+2]=uv[(row/2)*w+x+1];dst[2*x+3]=y[row*w+x+1];}
            }
            send_all(output,packed,size);
        }
        xioctl(fd,VIDIOC_QBUF,&b,"requeue buffer");
    }
}
