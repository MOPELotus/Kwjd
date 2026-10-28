"""Native1x parsed-face ROI fusion, adapted from facexlib0.3.0.

MIT License
Copyright (c) 2020 Xintao Wang

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Only supports BGR uint8,512x512 restored faces,upscale_factor1,use_parse=True.
Uses original ParseNet/classes/two101px Gaussian blurs/10px border. Multi-face
blending remains sequential in float32, with one final uint8 conversion.
"""


def _maps(matrix,box):
    """OpenCV warpAffine's bilinear fixed-point map at global destination phase.

Shifting an affine to an ROI changes its independently rounded x/y terms and
can shift sampling by1/32pixel. Keep global coordinates and the original
10-bit affine rounding, then use the same5-bit bilinear remap table instead.
warpAffine flags=INTER_AREA(3) is normalized to INTER_LINEAR internally.
    """
    import cv2
    import numpy as np
    left,top,right,bottom=box
    inverse=cv2.invertAffineTransform(matrix.astype(np.float64))
    x=np.arange(left,right,dtype=np.float64);y=np.arange(top,bottom,dtype=np.float64)
    def coordinates(row):
        delta=np.rint(inverse[row,0]*x*1024).astype(np.int64)
        base=np.rint((inverse[row,1]*y+inverse[row,2])*1024).astype(np.int64)+16
        return (base[:,None]+delta[None,:])>>5
    sx=coordinates(0);sy=coordinates(1)
    xy=np.stack((np.clip(sx>>5,-32768,32767),np.clip(sy>>5,-32768,32767)),axis=-1).astype(np.int16)
    alpha=(((sy&31)<<5)+(sx&31)).astype(np.uint16)
    return xy,alpha


def paste_faces_roi(helper):
    import cv2
    import numpy as np
    import torch
    from torchvision.transforms.functional import normalize
    from facexlib.utils.misc import img2tensor
    image=helper.input_img
    if (helper.upscale_factor!=1 or not helper.use_parse or image.dtype!=np.uint8
            or image.ndim!=3 or image.shape[2]!=3 or helper.face_size!=(512,512)):
        raise ValueError('ROI fusion only supports native1x BGRu8,parsed512face')
    if len(helper.restored_faces)!=len(helper.inverse_affine_matrices):
        raise ValueError('Restored face/affine counts differ')
    height,width=image.shape[:2];base=image.astype(np.float32)
    for face,matrix in zip(helper.restored_faces,helper.inverse_affine_matrices):
        if face.shape!=(512,512,3) or face.dtype!=np.uint8:
            raise ValueError('Expected512x512 BGRu8 restored face')
        matrix=np.asarray(matrix)
        if matrix.shape!=(2,3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix[:,:2]))<1e-12:
            raise ValueError('Expected finite nonsingular face affine')
        face_input=cv2.resize(face,(512,512),interpolation=cv2.INTER_LINEAR)
        face_input=img2tensor(face_input.astype('float32')/255.,bgr2rgb=True,float32=True)
        normalize(face_input,(.5,.5,.5),(.5,.5,.5),inplace=True)
        face_input=torch.unsqueeze(face_input,0).to(helper.device)
        with torch.no_grad():out=helper.face_parse(face_input)[0]
        labels=out.argmax(dim=1).squeeze().cpu().numpy()
        mask=np.zeros(labels.shape,dtype=np.float32)
        for index,color in enumerate([0,255,255,255,255,255,255,255,255,255,255,255,255,255,0,255,0,0,0]):
            mask[labels==index]=color
        mask=cv2.GaussianBlur(mask,(101,101),11)
        mask=cv2.GaussianBlur(mask,(101,101),11)
        mask[:10,:]=0;mask[-10:,:]=0;mask[:,:10]=0;mask[:,-10:]=0
        mask=mask/255.
        mask=cv2.resize(mask,face.shape[:2])
        # Include bilinear source support plus conservative rounding padding.
        corners=np.array([[-1,-1],[512,-1],[512,512],[-1,512]],np.float64)
        projected=corners@matrix[:,:2].T+matrix[:,2]
        left=max(0,int(np.floor(projected[:,0].min()))-2)
        right=min(width,int(np.ceil(projected[:,0].max()))+3)
        top=max(0,int(np.floor(projected[:,1].min()))-2)
        bottom=min(height,int(np.ceil(projected[:,1].max()))+3)
        if left>=right or top>=bottom:continue  # ParseNet still ran for this face.
        xy,alpha=_maps(matrix,(left,top,right,bottom))
        restored=cv2.remap(face,xy,alpha,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
        soft=cv2.remap(mask,xy,alpha,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)[:,:,None]
        target=base[top:bottom,left:right]
        target[:]=soft*restored+(1-soft)*target
    return base.astype(np.uint8)
