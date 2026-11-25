import '../src/lib/fonts.css';
import '../dist/theme.css';
import '../src/lib/base.css';
import { mount } from 'svelte';
import Gallery from './Gallery.svelte';
mount(Gallery, { target: document.getElementById('app') });
