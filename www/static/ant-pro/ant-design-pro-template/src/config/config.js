let baseUrl = '';
let imageUploadUrl = '';
if(process.env.NODE_ENV == 'dev') {
	baseUrl = 'http://gr-debug.goodrain.com/';
}else if(process.env.NODE_ENV == 'development'){
	// baseUrl = '/api';
	//baseUrl = 'http://127.0.0.1:8000';
	baseUrl = 'http://dev.goodrain.org';
}else if(process.env.NODE_ENV == 'production'){
	baseUrl = '';
}

imageUploadUrl = baseUrl + '/console/files/upload';
const config = {
	baseUrl: baseUrl,
	imageUploadUrl: imageUploadUrl
}
export default config